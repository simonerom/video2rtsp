from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from urllib.parse import urlparse

LOGGER = logging.getLogger("video2rtsp")
YTDLP_FORMAT = "best*[vcodec!=none][acodec!=none]/best"


class Video2RtspError(RuntimeError):
    """Raised when the CLI cannot continue."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video2rtsp",
        description="Expose a YouTube video as a local RTSP endpoint.",
    )
    parser.add_argument("url", help="YouTube URL or direct media URI")
    parser.add_argument("port", type=_port_number, help="Local RTSP port to listen on")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Local bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--path",
        default="/stream",
        help="RTSP mount path (default: /stream)",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Treat the input URL as a playable media URI and skip yt-dlp",
    )
    parser.add_argument(
        "--video-bitrate",
        type=_positive_int,
        default=2500,
        metavar="KBIT_S",
        help="Output H.264 bitrate in kbit/s (default: 2500)",
    )
    parser.add_argument(
        "--audio-bitrate",
        type=_positive_int,
        default=128000,
        metavar="BIT_S",
        help="Output AAC bitrate in bit/s (default: 128000)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logs",
    )
    return parser


def _port_number(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return number


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")


def ensure_command(name: str) -> None:
    if shutil.which(name) is None:
        raise Video2RtspError(f"Missing required command: {name}")


def is_media_uri(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme)


def resolve_source_uri(url: str, direct: bool) -> str:
    if direct:
        if not is_media_uri(url):
            raise Video2RtspError(
                "--direct requires a fully qualified media URI such as file:///tmp/video.mp4"
            )
        return url

    ensure_command("yt-dlp")
    command = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--get-url",
        "--format",
        YTDLP_FORMAT,
        url,
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        raise Video2RtspError(f"yt-dlp could not resolve the media URL: {details}") from exc

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise Video2RtspError("yt-dlp returned an empty media URL")
    if len(lines) > 1:
        LOGGER.warning("yt-dlp returned multiple URLs; using the first one")
    return lines[0]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    try:
        from .server import ServerConfig, endpoint_for, normalise_mount_path, serve_forever

        source_uri = resolve_source_uri(args.url, direct=args.direct)
        config = ServerConfig(
            source_uri=source_uri,
            host=args.host,
            port=args.port,
            path=normalise_mount_path(args.path),
            video_bitrate_kbps=args.video_bitrate,
            audio_bitrate_bps=args.audio_bitrate,
        )
        print(f"Source URI: {source_uri}", file=sys.stderr)
        print(f"RTSP endpoint: {endpoint_for(config)}", file=sys.stderr)
        serve_forever(config)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
