import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread
from typing import List, Optional, Set
from logging.handlers import RotatingFileHandler


logger = logging.getLogger("radio_recorder")


class FlushingRotatingFileHandler(RotatingFileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            self.flush()
        except Exception:
            pass


class RecorderConfig:
    def __init__(
        self,
        stream_url: str,
        output_dir: Path,
        segment_seconds: int = 600,
        station_name: str = "station",
        audio_bitrate: str = "128k",
    ) -> None:
        self.stream_url = stream_url
        self.output_dir = output_dir
        self.segment_seconds = segment_seconds
        self.station_name = station_name
        self.audio_bitrate = audio_bitrate


class FFmpegRecorder:
    """Wrapper around ffmpeg for recording and segmenting a live stream."""

    def __init__(self, config: RecorderConfig) -> None:
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self._shutdown = False
        self._last_run_duration: float = 0.0
        # Track files we have already logged as created
        self._known_files: Set[Path] = set(
            p for p in self.config.output_dir.glob("*.mp3") if p.is_file()
        )

    def build_command(self) -> List[str]:
        """
        Build an ffmpeg command that:
        - connects to the given stream URL
        - transcodes (if needed) to MP3
        - segments output into time-based chunks with wall-clock timestamps
        """
        output_pattern = self.config.output_dir / f"{self.config.station_name}_%Y%m%d_%H%M%S.mp3"

        cmd: List[str] = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            # More robust reconnection for flaky HTTP streams
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            self.config.stream_url,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-b:a",
            self.config.audio_bitrate,
            "-f",
            "segment",
            "-segment_time",
            str(self.config.segment_seconds),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            str(output_pattern),
        ]
        return cmd

    def _watch_output_dir(self, stop_event: Event) -> None:
        """
        Background watcher that logs when new MP3 files appear in the output directory.

        This does not affect recording; it's purely for observability.
        """
        poll_interval = 2.0
        while not stop_event.is_set():
            try:
                current_files = {
                    p for p in self.config.output_dir.glob("*.mp3") if p.is_file()
                }
                new_files = sorted(current_files - self._known_files)
                for path in new_files:
                    logger.info("New segment written: %s", path.name)
                if new_files:
                    self._known_files.update(new_files)
            except OSError as exc:
                logger.warning("Error while watching output directory for new segments: %s", exc)
            stop_event.wait(poll_interval)

    def start_once(self) -> int:
        """Start a single ffmpeg recording session and wait for it to exit."""
        cmd = self.build_command()
        logger.info("Attempting to connect to stream via ffmpeg.")
        logger.debug("ffmpeg command: %s", " ".join(cmd))
        watcher_stop = Event()
        watcher_thread = Thread(
            target=self._watch_output_dir,
            args=(watcher_stop,),
            name="segment-watcher",
            daemon=True,
        )
        start_ts = time.monotonic()
        try:
            self.process = subprocess.Popen(cmd)
            watcher_thread.start()
            exit_code = self.process.wait()
            self._last_run_duration = time.monotonic() - start_ts
            return exit_code
        finally:
            watcher_stop.set()
            watcher_thread.join(timeout=5.0)
            self.process = None

    def request_shutdown(self) -> None:
        """Signal handler callback to initiate a graceful shutdown."""
        if self._shutdown:
            return
        self._shutdown = True
        logger.info("Shutdown requested, stopping recorder...")
        if self.process and self.process.poll() is None:
            logger.info("Terminating ffmpeg process...")
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                logger.warning("ffmpeg did not exit in time, killing...")
                self.process.kill()

    def run_forever(self) -> None:
        """
        Run ffmpeg in a loop.

        If ffmpeg exits unexpectedly (e.g., network glitch), wait a bit and restart,
        unless a shutdown has been requested.
        """
        backoff_seconds = 5
        while not self._shutdown:
            exit_code = self.start_once()
            if self._shutdown:
                logger.info("Recorder stopped gracefully.")
                break

            duration = self._last_run_duration
            if duration < 10:
                # Exited very quickly – likely failed to connect or negotiate stream
                logger.warning(
                    "Connection attempt failed quickly (%.1f seconds, exit code %s). "
                    "Will retry after %s seconds.",
                    duration,
                    exit_code,
                    backoff_seconds,
                )
            else:
                # Ran for a while then exited – treat as lost connection
                level = logging.ERROR if exit_code != 0 else logging.WARNING
                logger.log(
                    level,
                    "Connection to stream was lost after %.1f seconds (exit code %s). "
                    "Will retry after %s seconds.",
                    duration,
                    exit_code,
                    backoff_seconds,
                )

            time.sleep(backoff_seconds)


def parse_args(argv: Optional[list] = None) -> RecorderConfig:
    parser = argparse.ArgumentParser(
        description="Record a live radio stream and segment it into MP3 chunks."
    )
    parser.add_argument(
        "--stream-url",
        dest="stream_url",
        default=os.getenv("STREAM_URL"),
        help="URL of the radio stream (HLS .m3u8 or direct HTTP). "
        "Can also be provided via STREAM_URL env var.",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=os.getenv("OUTPUT_DIR", "./recordings"),
        help="Directory to write MP3 chunks to (default: ./recordings). "
        "Can also be provided via OUTPUT_DIR env var.",
    )
    parser.add_argument(
        "--segment-seconds",
        dest="segment_seconds",
        type=int,
        default=int(os.getenv("SEGMENT_SECONDS", "600")),
        help="Length of each segment in seconds (default: 600 / 10 minutes).",
    )
    parser.add_argument(
        "--station-name",
        dest="station_name",
        default=os.getenv("STATION_NAME", "station"),
        help="Logical name of the station, used as filename prefix.",
    )
    parser.add_argument(
        "--audio-bitrate",
        dest="audio_bitrate",
        default=os.getenv("AUDIO_BITRATE", "128k"),
        help="Output MP3 bitrate (default: 128k).",
    )

    args = parser.parse_args(argv)

    if not args.stream_url:
        parser.error("stream URL must be provided via --stream-url or STREAM_URL env var.")

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    return RecorderConfig(
        stream_url=args.stream_url,
        output_dir=output_dir,
        segment_seconds=args.segment_seconds,
        station_name=args.station_name,
        audio_bitrate=args.audio_bitrate,
    )


def setup_logging() -> None:
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers if setup_logging is called more than once
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Optional file handler
    log_file_path = os.getenv("LOG_FILE_PATH", "recorder.log")
    try:
        file_handler = FlushingRotatingFileHandler(
            log_file_path,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError:
        # If we cannot write to the log file, continue with console-only logging
        logger.warning("Could not create log file at %s; continuing without file logging.", log_file_path)


def main(argv: Optional[list] = None) -> int:
    setup_logging()
    config = parse_args(argv)

    logger.info(
        "Starting recorder: url=%s, output_dir=%s, segment_seconds=%s, station=%s",
        config.stream_url,
        config.output_dir,
        config.segment_seconds,
        config.station_name,
    )

    recorder = FFmpegRecorder(config)

    def _handle_signal(signum, frame) -> None:  # type: ignore[override]
        logger.info("Received signal %s", signum)
        recorder.request_shutdown()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        recorder.run_forever()
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down...")
        recorder.request_shutdown()

    logger.info("Recorder exited.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

