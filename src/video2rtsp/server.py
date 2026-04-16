from __future__ import annotations

import logging
import signal
import subprocess
from dataclasses import dataclass

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")

from gi.repository import GLib, Gst, GstRtspServer

Gst.init(None)

LOGGER = logging.getLogger("video2rtsp")


class RtspServerError(RuntimeError):
    """Raised when the embedded RTSP server cannot be started."""


@dataclass(frozen=True)
class ServerConfig:
    source_uri: str
    host: str
    port: int
    path: str
    video_bitrate_kbps: int = 2500
    audio_bitrate_bps: int = 128000
    loop: bool = False


def normalise_mount_path(path: str) -> str:
    cleaned = path.strip() or "/stream"
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    return cleaned


def endpoint_for(config: ServerConfig) -> str:
    return f"rtsp://{config.host}:{config.port}{config.path}"


def preview_command(endpoint: str) -> list[str]:
    return [
        "ffplay",
        "-window_title",
        "video2rtsp preview",
        "-rtsp_transport",
        "tcp",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        endpoint,
    ]


def _make(factory_name: str, name: str | None = None) -> Gst.Element:
    element = Gst.ElementFactory.make(factory_name, name)
    if element is None:
        raise RtspServerError(f"Missing GStreamer element: {factory_name}")
    return element


def _make_optional(factory_name: str, name: str | None = None) -> Gst.Element | None:
    return Gst.ElementFactory.make(factory_name, name)


def _link_many(*elements: Gst.Element) -> None:
    for left, right in zip(elements, elements[1:]):
        if not left.link(right):
            raise RtspServerError(
                f"Could not link GStreamer elements {left.name} -> {right.name}"
            )


class UriRtspFactory(GstRtspServer.RTSPMediaFactory):
    def __init__(self, config: ServerConfig) -> None:
        super().__init__()
        self._config = config
        self._contexts: dict[int, dict[str, object]] = {}
        self._bus_watchers: list[Gst.Bus] = []
        self._loop_durations: dict[int, int] = {}
        self._loop_attempts: dict[int, int] = {}
        self.set_shared(True)
        self.set_suspend_mode(GstRtspServer.RTSPSuspendMode.NONE)
        self.connect("media-configure", self._on_media_configure)

    def do_create_element(self, url: object) -> Gst.Element:
        source_bin = Gst.Bin.new("video2rtsp-source")
        if source_bin is None:
            raise RtspServerError("Could not create the GStreamer source bin")

        source = self._new_source(source_bin)
        source_bin.add(source)

        video_sink = self._add_video_branch(source_bin)
        audio_sink = self._add_audio_branch(source_bin)
        self._contexts[id(source_bin)] = {
            "video": False,
            "audio": False,
            "video_sink": video_sink,
            "audio_sink": audio_sink,
            "source": source,
            "restarting": False,
        }
        return source_bin

    def _new_source(self, source_bin: Gst.Bin) -> Gst.Element:
        source = _make("uridecodebin", "source")
        source.set_property("uri", self._config.source_uri)
        source.connect("pad-added", self._on_pad_added, source_bin)
        source.connect("drained", self._on_source_drained, source_bin)
        return source

    def _on_media_configure(self, factory: object, media: GstRtspServer.RTSPMedia) -> None:
        element = media.get_element()
        if element is None:
            return

        bus = element.get_bus()
        if bus is None:
            return

        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message, element)
        self._bus_watchers.append(bus)

        if self._config.loop:
            self._loop_attempts[id(element)] = 0
            GLib.timeout_add(100, self._enable_segment_looping, element)

    def _on_bus_message(
        self,
        bus: Gst.Bus,
        message: Gst.Message,
        element: Gst.Element,
    ) -> None:
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            LOGGER.error("GStreamer error: %s", error.message)
            if debug:
                LOGGER.debug("GStreamer debug: %s", debug)
        elif message.type == Gst.MessageType.WARNING:
            error, debug = message.parse_warning()
            LOGGER.warning("GStreamer warning: %s", error.message)
            if debug:
                LOGGER.debug("GStreamer debug: %s", debug)
        elif message.type == Gst.MessageType.SEGMENT_DONE:
            duration = self._loop_durations.get(id(element))
            if duration:
                LOGGER.info("Looping source back to the beginning")
                if not self._seek_segment(element, duration):
                    LOGGER.error("Could not restart the source for looping")
        elif message.type == Gst.MessageType.EOS:
            LOGGER.info("Source stream ended")

    def _on_source_drained(self, source: Gst.Element, source_bin: Gst.Bin) -> None:
        LOGGER.info("Source stream ended")
        if not self._config.loop:
            return

        context = self._contexts[id(source_bin)]
        if context["restarting"]:
            return

        LOGGER.info("Looping source back to the beginning")
        context["restarting"] = True
        GLib.idle_add(self._restart_source, source_bin)

    def _restart_source(self, source_bin: Gst.Bin) -> bool:
        context = self._contexts[id(source_bin)]
        old_source = context["source"]

        if not isinstance(old_source, Gst.Element):
            context["restarting"] = False
            return False

        old_source.set_state(Gst.State.NULL)
        source_bin.remove(old_source)

        context["video"] = False
        context["audio"] = False

        new_source = self._new_source(source_bin)
        source_bin.add(new_source)
        new_source.sync_state_with_parent()

        context["source"] = new_source
        context["restarting"] = False
        return False

    def _enable_segment_looping(self, element: Gst.Element) -> bool:
        attempts = self._loop_attempts.get(id(element), 0) + 1
        self._loop_attempts[id(element)] = attempts

        success, duration = element.query_duration(Gst.Format.TIME)
        if not success or duration <= 0 or duration == Gst.CLOCK_TIME_NONE:
            if attempts >= 50:
                LOGGER.warning("Could not determine a finite duration for loop mode")
                return False
            return True

        self._loop_durations[id(element)] = duration
        if not self._seek_segment(element, duration):
            LOGGER.error("Could not enable loop mode for this source")
        return False

    @staticmethod
    def _seek_segment(element: Gst.Element, duration: int) -> bool:
        return element.seek(
            1.0,
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.SEGMENT | Gst.SeekFlags.KEY_UNIT,
            Gst.SeekType.SET,
            0,
            Gst.SeekType.SET,
            duration,
        )

    def _on_pad_added(
        self,
        source: Gst.Element,
        pad: Gst.Pad,
        source_bin: Gst.Bin,
    ) -> None:
        context = self._contexts[id(source_bin)]
        caps = pad.get_current_caps() or pad.query_caps(None)
        if caps is None or caps.get_size() == 0:
            return

        media_type = caps.get_structure(0).get_name()
        if media_type.startswith("video/") and not context["video"]:
            self._link_pad_or_raise(
                pad,
                context["video_sink"],
                "video",
            )
            context["video"] = True
            LOGGER.info("Attached video branch from %s", self._config.source_uri)
        elif media_type.startswith("audio/") and not context["audio"]:
            self._link_pad_or_raise(
                pad,
                context["audio_sink"],
                "audio",
            )
            context["audio"] = True
            LOGGER.info("Attached audio branch from %s", self._config.source_uri)

    def _add_video_branch(self, source_bin: Gst.Bin) -> Gst.Pad | None:
        queue = _make("queue", "video_queue")
        convert = _make("videoconvert", "video_convert")
        scale = _make("videoscale", "video_scale")
        encoder = _make("x264enc", "video_encoder")
        parser = _make_optional("h264parse", "video_parser")
        pay = _make("rtph264pay", "pay0")

        encoder.set_property("bitrate", self._config.video_bitrate_kbps)
        encoder.set_property("key-int-max", 60)
        encoder.set_property("speed-preset", "ultrafast")
        encoder.set_property("tune", "zerolatency")
        encoder.set_property("byte-stream", True)

        pay.set_property("pt", 96)
        pay.set_property("config-interval", 1)

        elements = [queue, convert, scale, encoder]
        if parser is not None:
            elements.append(parser)
        elements.append(pay)

        for element in elements:
            source_bin.add(element)

        _link_many(*elements)
        return queue.get_static_pad("sink")

    def _add_audio_branch(self, source_bin: Gst.Bin) -> Gst.Pad | None:
        queue = _make("queue", "audio_queue")
        convert = _make("audioconvert", "audio_convert")
        resample = _make("audioresample", "audio_resample")
        encoder = _make("avenc_aac", "audio_encoder")
        parser = _make_optional("aacparse", "audio_parser")
        pay = _make("rtpmp4gpay", "pay1")

        encoder.set_property("bitrate", self._config.audio_bitrate_bps)
        pay.set_property("pt", 97)

        elements = [queue, convert, resample, encoder]
        if parser is not None:
            elements.append(parser)
        elements.append(pay)

        for element in elements:
            source_bin.add(element)

        _link_many(*elements)
        return queue.get_static_pad("sink")

    @staticmethod
    def _link_pad_or_raise(
        source_pad: Gst.Pad,
        sink_pad: Gst.Pad | None,
        branch_name: str,
    ) -> None:
        if sink_pad is None:
            raise RtspServerError(f"Could not create {branch_name} branch sink pad")

        result = source_pad.link(sink_pad)
        if result != Gst.PadLinkReturn.OK:
            raise RtspServerError(
                f"Could not link source pad to {branch_name} branch: {result.value_nick}"
            )


def serve_forever(config: ServerConfig, preview: bool = False) -> None:
    server = GstRtspServer.RTSPServer()
    server.set_address(config.host)
    server.set_service(str(config.port))

    mounts = server.get_mount_points()
    if mounts is None:
        raise RtspServerError("Could not access RTSP mount points")

    mounts.add_factory(config.path, UriRtspFactory(config))
    source_id = server.attach(None)
    if source_id <= 0:
        raise RtspServerError("Could not attach the RTSP server to the GLib main loop")

    loop = GLib.MainLoop()
    preview_process: subprocess.Popen[bytes] | None = None

    def launch_preview() -> bool:
        nonlocal preview_process
        stream_endpoint = endpoint_for(config)
        LOGGER.info("Opening preview window for %s", stream_endpoint)
        preview_process = subprocess.Popen(preview_command(stream_endpoint))
        return False

    def stop_loop(*_: object) -> None:
        if preview_process is not None and preview_process.poll() is None:
            preview_process.terminate()
        if loop.is_running():
            loop.quit()

    signal.signal(signal.SIGINT, stop_loop)
    signal.signal(signal.SIGTERM, stop_loop)

    if preview:
        GLib.timeout_add(250, launch_preview)

    LOGGER.info("Serving %s", endpoint_for(config))
    loop.run()
