# video2rtsp

`video2rtsp` prende un URL YouTube e lo ripubblica come stream RTSP locale.

E` utile quando hai un player, un NVR, un bridge domotico o un software video che sa leggere RTSP ma non URL web diretti.

## A chi serve

- Home Assistant, NVR e automazioni che accettano RTSP
- VLC, ffplay, OBS e client che preferiscono un endpoint RTSP
- Tooling locale e test bench che devono consumare un video YouTube come se fosse una camera IP

## Come funziona

1. risolve il media URL con `yt-dlp`
2. apre il flusso con GStreamer
3. lo ri-encoda in H.264/AAC
4. lo serve via RTSP su `localhost`

## Requisiti

- Python 3.11+
- `yt-dlp`
- GStreamer con `PyGObject`, `Gst` e `GstRtspServer`
- plugin GStreamer per H.264/AAC: `x264enc`, `avenc_aac`, `rtph264pay`, `rtpmp4gpay`

## Installazione

Da checkout locale:

```bash
pip install -e .
```

Oppure da GitHub:

```bash
pip install git+https://github.com/simonerom/video2rtsp.git
```

## Uso

```bash
video2rtsp "https://www.youtube.com/watch?v=jNQXAC9IVRw" 8554
```

Endpoint di default:

```text
rtsp://127.0.0.1:8554/stream
```

## Opzioni utili

- `--host 0.0.0.0` per esporlo fuori da localhost
- `--path /live` per cambiare il mount RTSP
- `--video-bitrate 1200` per ridurre CPU e banda
- `--direct` per usare direttamente un media URI, ad esempio `file:///tmp/video.mp4`

## Esempi

```bash
video2rtsp "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 8554
ffplay rtsp://127.0.0.1:8554/stream
```

```bash
video2rtsp --direct "file:///tmp/sample.mp4" 8554
```
