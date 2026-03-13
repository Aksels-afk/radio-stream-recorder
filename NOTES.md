## NOTES

This file is an informal log of implementation thoughts and trade-offs.

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

