# video2rtsp

`video2rtsp` takes a YouTube URL and republishes it as a local RTSP stream.

It is useful when you have a player, NVR, home automation bridge, or video tool that can consume RTSP but not direct web URLs.

## Who It Is For

- Home Assistant, NVRs, and automation systems that accept RTSP
- VLC, ffplay, OBS, and clients that prefer an RTSP endpoint
- Local tooling and test benches that need to consume a YouTube video as if it were an IP camera

## How It Works

1. Resolves the media URL with `yt-dlp`
2. Opens the source with GStreamer
3. Re-encodes it to H.264/AAC
4. Serves it over RTSP on `localhost`

## Requirements

- Python 3.11+
- `yt-dlp`
- GStreamer with `PyGObject`, `Gst`, and `GstRtspServer`
- GStreamer plugins for H.264/AAC: `x264enc`, `avenc_aac`, `rtph264pay`, `rtpmp4gpay`

## Installation

From a local checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Or directly from GitHub:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install git+https://github.com/simonerom/video2rtsp.git
```

## Usage

```bash
video2rtsp "https://www.youtube.com/watch?v=jNQXAC9IVRw" 8554
```

Default endpoint:

```text
rtsp://127.0.0.1:8554/stream
```

## Useful Options

- `--host 0.0.0.0` to expose it outside localhost
- `--path /live` to change the RTSP mount path
- `--video-bitrate 1200` to reduce CPU and bandwidth usage
- `--direct` to use a media URI directly, for example `file:///tmp/video.mp4`

## Examples

```bash
video2rtsp "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 8554
ffplay rtsp://127.0.0.1:8554/stream
```

```bash
video2rtsp --direct "file:///tmp/sample.mp4" 8554
```
