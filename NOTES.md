## NOTES


TODO:
1. Python app that records radio streams and saves chunked 10-minute MP3 files. Must actually work
2. Containerize the app.
3. Correct README of setup instructions
4. Azure deployment plan
5. notes (this file)

This file is an informal log of implementation thoughts and trade-offs.



Used ffprobe "https://stream3.ehrhiti.lv:8000/Stream_93_LV01.aac" and got specs of the stream:
Codec AAC
Sample rate: 44100 Hz
Channels: stereo
Bitrate 101kb/s

EHR stream needs to be re-encoded to MP3 from AAC. (Source: 101kbps AAC stereo @ 44.1 kHz, Output: 128kbps MP3 stereo @ 44.1 kHz.)
dimensonesuonoroma stream needs to be re-encoded to MP3 from HLS AAC. (Source: 64 kbps AAC stereo @ 48 kHz, Output: 128 kbps MP3 stereo @ 48 kHz.)

Bitrate configurable in main.py class RecorderConfig. Not that much focus on compression necessary.

All timestamps in logs and filenames are in UTC to for simpler operations.

Tested both HLS and AAC streams. Both successfully write to mp3 files with correct formats, locally and docker.

What happens if I turn off internet connection while app is running and actively writing a segment??
Looks like it recovers nicely, continuing from the point of last connection in the same segment.

Made a logger which logs all actions performed by the code, including connections to streams and saved files and their names.

- **Choice of ffmpeg vs pure Python audio handling**
  - Decoding and segmenting AAC/HLS streams directly in Python requires a stack of libraries (e.g., `ffmpeg-python`, `pydub`, `PyAV`) and careful handling of edge cases.
  - `ffmpeg` is already battle-tested for:
    - Handling HLS playlists and network quirks.
    - Transcoding between codecs.
    - Time-based segmentation with safe file rollover.
  - The Python code is intentionally thin, treating `ffmpeg` as the streaming/codec engine and focusing on configuration, lifecycle, and resilience.

- **Segment strategy**
  - Using `ffmpeg`'s `segment` muxer with `-segment_time` and `-strftime 1` allows:
    - Clean, time-based cuts.
    - File names that map directly to wall-clock capture time.
  - This makes it easier for downstream systems to reason about time ranges and perform partial re-processing if needed.

- **Graceful shutdown**
  - The Python process installs signal handlers for `SIGINT` and `SIGTERM`, then calls `terminate()` on the `ffmpeg` process and waits with a timeout before resorting to `kill()`.
  - The goal is to ensure that whatever data has already been written to the final MP3 file is properly finalized rather than left as a corrupt partial.

- **Resilience to stream flakiness**
  - The recorder runs `ffmpeg` in a loop:
    - If `ffmpeg` exits unexpectedly (non-zero code), the Python wrapper sleeps briefly and restarts.
    - If a shutdown has been requested (via signal), no restart occurs.
  - This keeps the core logic simple while providing basic self-healing for transient network issues.

- **Azure architecture notes**
  - Chose **Azure Container Apps** over AKS to reduce operational complexity for a small fleet of similar, long-running containers.
  - Blob Storage + Event Grid was chosen for:
    - Low friction integration.
    - Easy fan-out to multiple downstream consumers.
    - Clear, event-driven pipeline: "new chunk → event → processing".

