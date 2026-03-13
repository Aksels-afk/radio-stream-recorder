## Radio Stream Recorder

Python-based tool (packaged as a Docker container) for recording live radio streams (HLS or direct HTTP) and saving them as chunked MP3 files.

### What the tool does

- **Connects to a live stream**: Accepts an HLS (`.m3u8`) or direct HTTP audio URL.
- **Segments to MP3**: Uses `ffmpeg` to transcode if necessary and write fixed-length MP3 chunks.
- **Continuous recording**: Runs until stopped, auto-restarting `ffmpeg` on transient failures.
- **Graceful shutdown**: Handles `SIGINT`/`SIGTERM` so the current segment is finalized cleanly.

Chunks are written to a configurable local directory with filenames like:

```text
<station_name>_YYYYMMDD_HHMMSS.mp3
```

This layout is designed to be easy to sync or upload into object storage later (e.g., Azure Blob Storage).

### Requirements

- **Python**: 3.14.3 (3.12 used in Docker image)
- **ffmpeg**: Installed and available on the `PATH` when running outside Docker
- **Docker Desktop**: For containerized runs

### Configuration

You can configure the recorder via **CLI arguments** or **environment variables**.

- **Stream URL**
  - **CLI**: `--stream-url`
  - **Env**: `STREAM_URL`
  - **Description**: URL of the radio stream (HLS `.m3u8` or direct HTTP).

- **Output directory**
  - **CLI**: `--output-dir`
  - **Env**: `OUTPUT_DIR` (default: `./recordings`)
  - **Description**: Directory where MP3 chunks are stored.

- **Segment length**
  - **CLI**: `--segment-seconds`
  - **Env**: `SEGMENT_SECONDS` (default: `600` seconds = 10 minutes)
  - **Description**: Duration of each MP3 chunk.

- **Station name**
  - **CLI**: `--station-name`
  - **Env**: `STATION_NAME` (default: `station`)
  - **Description**: Prefix used in output filenames to distinguish stations.

- **Audio bitrate**
  - **CLI**: `--audio-bitrate`
  - **Env**: `AUDIO_BITRATE` (default: `128k`)
  - **Description**: Output MP3 bitrate.

- **Logging level**
  - **Env**: `LOG_LEVEL` (default: `INFO`)
  - **Description**: Standard Python logging level (`DEBUG`, `INFO`, `WARNING`, etc.).

### If you want to run the app locally (without Docker)

1. **Install dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   Ensure `ffmpeg` is installed on your system, for example on macOS:

   ```bash
   brew install ffmpeg
   ```

2. **Run the recorder**

   Example using the provided HLS test stream:

   ```bash
   export STREAM_URL="https://stream.dimensionesuonoroma.radio/audio/dsr.stream_aac64/playlist.m3u8"
   python -m radio_recorder.main --station-name dsr --segment-seconds 600 --output-dir ./recordings
   ```

   Example using the provided direct HTTP stream:

   ```bash
   export STREAM_URL="https://stream3.ehrhiti.lv:8000/Stream_93_LV01.aac"
   python -m radio_recorder.main --station-name ehr --segment-seconds 600 --output-dir ./recordings
   ```

   Stop with `Ctrl+C`. The current MP3 segment will be cleanly finalized before exit.

### Running with Docker

#### Build the image

From the project root:

```bash
docker build -t radio-recorder:latest .
```

#### Run the container

Create a local directory for recordings (if it does not exist):

```bash
mkdir -p ./recordings
```

Run against the HLS test stream:

```bash
docker run --rm \
  -e STREAM_URL="https://stream.dimensionesuonoroma.radio/audio/dsr.stream_aac64/playlist.m3u8" \
  -e STATION_NAME="dsr" \
  -e SEGMENT_SECONDS="600" \
  -e OUTPUT_DIR="/recordings" \
  -v "$(pwd)/recordings:/recordings" \
  radio-recorder:latest
```

Run against the direct HTTP test stream:

```bash
docker run --rm \
  -e STREAM_URL="https://stream3.ehrhiti.lv:8000/Stream_93_LV01.aac" \
  -e STATION_NAME="ehr" \
  -e SEGMENT_SECONDS="600" \
  -e OUTPUT_DIR="/recordings" \
  -v "$(pwd)/recordings:/recordings" \
  radio-recorder:latest
```

App can also be run from Docker desktop app, but Environment variables must be set up.
e.g. STREAM_URL = https://stream3.ehrhiti.lv:8000/Stream_93_LV01.aac

The container will keep running and writing chunks until you stop it (e.g. `Ctrl+C` if run in the foreground, or `docker stop` if run detached).

### Design notes

- **ffmpeg-based segmentation**: Instead of manually decoding and chopping audio frames in Python, the tool delegates that work to `ffmpeg`, which already handles:
  - Different container formats and codecs (AAC, MP3, etc.)
  - HLS playlists vs. HTTP streams
  - Reconnection behavior for flaky streams
- **Time-based MP3 chunks**: Using `ffmpeg`'s `segment` muxer with `strftime`-based filenames makes it easy to map files to wall-clock time and ingest them later into storage or downstream processing.
- **Graceful shutdown**: Signals are captured in the Python process, which then terminates `ffmpeg` and waits, so the current file is finalized instead of being left in a corrupted state.
- **Future storage integration**: The flat directory of time-stamped MP3 files is intentionally simple, so a later step can sync or upload chunks to Azure Blob Storage, S3, or another object store without changing the core recording logic.
- **Added Logging functionality**: Easily track and see what is exactly happening in the code in recorder.log. Tracks connection attempts, reconnects, shutdown, and per-segment creation.

### Azure deployment plan

See `AZURE_DEPLOYMENT.md` for a full plan to run multiple instances in Azure, store audio chunks, coordinate downstream consumers, and monitor the system.

