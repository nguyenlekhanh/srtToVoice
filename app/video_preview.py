"""Video preview logic (no UI code here).

Phase 4 only: probe a local video file for metadata and play it back
locally for preview purposes. The original video file is opened
read-only and is NEVER modified, copied, re-encoded or transcoded.

Implementation notes:

- Decoding uses PyAV (``av``), which bundles FFmpeg libraries as a
  wheel dependency. No system FFmpeg installation is required and no
  FFmpeg subprocess is spawned.
- Audio output uses ``sounddevice`` (PortAudio) so preview volume and
  mute can be controlled without touching the source file.
- Video frames are delivered to the UI through a callback
  (``on_frame``) that receives a PIL image; the Tkinter layer converts
  it to a PhotoImage.
- The generated voice WAVs from Phase 3 are completely independent of
  this module and never play together with the video preview.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    import av
except ImportError:  # pragma: no cover - dependency guard
    av = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency guard
    np = None

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - dependency guard
    sd = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover - dependency guard
    Image = None


class VideoError(Exception):
    """Human-readable video failure (safe to show in the UI)."""


#: File extensions accepted by the upload picker (lowercase, with dot).
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

#: Audio is resampled to this rate for preview playback.
_AUDIO_SAMPLE_RATE = 48000
#: Audio is played back in chunks of this many frames.
_AUDIO_BLOCK_SIZE = 1024
#: How far the audio clock may lead the video clock before we pause
#: audio writes to let video catch up (seconds).
_MAX_AV_DRIFT = 0.25


def format_timecode(seconds: float) -> str:
    """Format seconds as ``MM:SS`` or ``HH:MM:SS`` when necessary."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass(frozen=True)
class VideoInfo:
    """Lightweight metadata about an uploaded source video."""

    path: Path
    duration: float  # seconds
    width: int
    height: int
    fps: float
    has_audio: bool


def probe_video(path: Path) -> VideoInfo:
    """Read video metadata (duration, size, fps, audio presence).

    Opens the container read-only and inspects stream headers only —
    the video is never rendered or transcoded for this. Raises
    VideoError with a concise message on any failure.
    """
    if av is None:
        raise VideoError(
            "Video support is not installed. Run: pip install av"
        )
    path = Path(path)
    if not path.is_file():
        raise VideoError("Video file is missing.")
    if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        raise VideoError(
            f"Unsupported video format '{path.suffix}'. "
            "Supported: mp4, mov, mkv, webm, avi."
        )
    try:
        with av.open(str(path), mode="r") as container:
            video_stream = (
                container.streams.video[0] if container.streams.video else None
            )
            if video_stream is None:
                raise VideoError("This file has no video stream.")

            duration = float(container.duration or 0) / float(av.time_base)
            if duration <= 0 and video_stream.duration is not None:
                duration = float(video_stream.duration * video_stream.time_base)
            if duration <= 0:
                raise VideoError("Could not determine the video duration.")

            fps = 0.0
            rate = video_stream.average_rate or video_stream.guessed_rate
            if rate is not None and float(rate) > 0:
                fps = float(rate)

            return VideoInfo(
                path=path,
                duration=duration,
                width=int(video_stream.codec_context.width),
                height=int(video_stream.codec_context.height),
                fps=fps,
                has_audio=bool(container.streams.audio),
            )
    except VideoError:
        raise
    except av.error.FileNotFoundError:
        raise VideoError("Video file is missing.")
    except Exception as exc:
        raise VideoError(
            "Could not open this video. The file may be corrupt or use an "
            f"unsupported codec. ({type(exc).__name__})"
        ) from exc


class VideoPlayer:
    """Plays one source video for preview with A/V sync.

    All decoding and audio output happen on background threads; the UI
    only receives frame callbacks (PIL images) and never blocks. The
    source file is opened read-only and never modified.

    Callbacks (invoked from worker threads — marshal to the UI thread):
    - ``on_frame(image)``: a decoded video frame as a PIL RGB image.
    - ``on_tick(position, duration)``: current playback position.
    - ``on_finished()``: playback reached the end of the file.
    - ``on_error(message)``: a concise human-readable failure message.
    """

    def __init__(
        self,
        path: Path,
        on_frame: Optional[Callable[["Image.Image"], None]] = None,
        on_tick: Optional[Callable[[float, float], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        if av is None:
            raise VideoError(
                "Video support is not installed. Run: pip install av"
            )
        if Image is None:
            raise VideoError(
                "Image support is not installed. Run: pip install pillow"
            )
        self.info = probe_video(path)

        self._on_frame = on_frame
        self._on_tick = on_tick
        self._on_finished = on_finished
        self._on_error = on_error

        self._lock = threading.RLock()
        self._video_thread: Optional[threading.Thread] = None
        self._audio_thread: Optional[threading.Thread] = None
        self._audio_stream = None  # sd.RawOutputStream

        # Playback state (guarded by ``_lock``).
        self._playing = False
        self._paused = False
        self._stop_requested = False
        # Bug 2: each worker has its OWN pending-seek slot. A single
        # shared slot was consumed by whichever worker got there first,
        # so the other worker never re-seeked its container (stale
        # frames / silent or stalled audio after every timeline seek).
        self._video_seek_to: Optional[float] = None
        self._audio_seek_to: Optional[float] = None
        self._position = 0.0
        self._volume = 1.0
        self._muted = False
        self._audio_clock = 0.0  # where audio playback currently is
        self._audio_ready = False  # audio clock is meaningful
        self._video_start_wall: Optional[float] = None  # no-audio pacing
        self._video_start_pts: Optional[float] = None

    # ------------------------------------------------------------ state

    @property
    def duration(self) -> float:
        return self.info.duration

    @property
    def position(self) -> float:
        with self._lock:
            return self._position

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing and not self._paused

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._playing and self._paused

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._playing

    @property
    def volume(self) -> float:
        with self._lock:
            return self._volume

    @property
    def muted(self) -> bool:
        with self._lock:
            return self._muted

    # ---------------------------------------------------------- controls

    def play(self) -> None:
        """Start or resume playback (no-op if already playing)."""
        with self._lock:
            if self._playing and not self._paused:
                return
            if self._playing and self._paused:
                self._paused = False
                self._video_start_wall = None  # re-anchor pacing clock
                return
            # Bug 2: a fresh start after playback reached the end must
            # restart from the beginning; the stale audio clock would
            # otherwise make the new playback fast-forward to the end.
            if self._position >= self.duration:
                self._position = 0.0
            # Bug 2: re-anchor the sync clocks to the start position and
            # enqueue initial seeks, so playback starts where the user
            # seeked while stopped (previously the workers always opened
            # the container at t=0 and the position snapped back to 0).
            self._audio_clock = self._position
            self._audio_ready = False
            if self._position > 0.0:
                self._video_seek_to = self._position
                self._audio_seek_to = self._position
            self._playing = True
            self._paused = False
            self._stop_requested = False
            self._video_start_wall = None
            self._video_start_pts = None
        self._start_threads()

    def pause(self) -> None:
        """Pause playback (no-op if not playing)."""
        with self._lock:
            if not self._playing:
                return
            self._paused = True
        # Bug 2: discard the audio already buffered in the output stream
        # so sound stops immediately on pause (no buffered tail keeps
        # playing). The workers hold their current frame while paused.
        self._flush_audio_stream()

    def stop(self) -> None:
        """Stop playback and reset to the beginning."""
        with self._lock:
            self._stop_requested = True
            self._paused = False
        self._join_threads(timeout=2.0)
        with self._lock:
            self._playing = False
            self._position = 0.0
            self._audio_clock = 0.0
            self._audio_ready = False
            self._video_seek_to = None
            self._audio_seek_to = None
            self._video_start_wall = None
            self._video_start_pts = None

    def seek(self, seconds: float) -> None:
        """Seek to ``seconds`` (clamped to the video duration)."""
        seconds = max(0.0, min(float(seconds), self.duration))
        with self._lock:
            self._position = seconds
            if self._playing:
                # Bug 2: enqueue the seek for BOTH workers so each one
                # re-seeks its own container (the old single shared slot
                # was consumed by only one of the two). Each worker
                # drops content before the target instead of playing it.
                self._video_seek_to = seconds
                self._audio_seek_to = seconds
            else:
                # Not playing: remember the position; playback starts
                # from there when the user presses Play.
                self._audio_clock = seconds
                self._audio_ready = False
                self._video_start_wall = None
                self._video_start_pts = None

    def set_volume(self, volume: float) -> None:
        """Set preview volume (0.0 .. 1.0). Never touches the source."""
        with self._lock:
            self._volume = max(0.0, min(1.0, float(volume)))

    def set_muted(self, muted: bool) -> None:
        """Mute or unmute preview audio. Never touches the source."""
        with self._lock:
            self._muted = bool(muted)

    def close(self) -> None:
        """Stop playback and release all resources."""
        self.stop()

    def show_first_frame(self) -> None:
        """Decode and emit the first video frame without starting playback.

        Used to show a poster image in the preview area as soon as a
        video is uploaded. The source file is opened read-only.
        """
        if av is None:
            return
        try:
            container = av.open(str(self.info.path), mode="r")
        except Exception:
            return
        try:
            stream = container.streams.video[0]
            for packet in container.demux(stream):
                if packet.size == 0 and packet.dts is None:
                    break
                try:
                    for frame in packet.decode():
                        image = frame.to_image()
                        if self._on_frame is not None:
                            self._on_frame(image)
                        return
                except Exception:
                    continue
        finally:
            container.close()

    # ----------------------------------------------------------- workers

    def _start_threads(self) -> None:
        self._video_thread = threading.Thread(
            target=self._video_worker, daemon=True
        )
        self._video_thread.start()
        if self.info.has_audio and sd is not None:
            self._audio_thread = threading.Thread(
                target=self._audio_worker, daemon=True
            )
            self._audio_thread.start()

    def _join_threads(self, timeout: float) -> None:
        for thread in (self._video_thread, self._audio_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=timeout)
        self._video_thread = None
        self._audio_thread = None

    def _current_gain(self) -> float:
        with self._lock:
            if self._muted:
                return 0.0
            return self._volume

    def _consume_seek(self, attr: str) -> Optional[float]:
        """Pop the pending seek for ONE worker (Bug 2: per-worker slot)."""
        with self._lock:
            target = getattr(self, attr)
            setattr(self, attr, None)
            return target

    def _should_quit(self) -> bool:
        with self._lock:
            return self._stop_requested or not self._playing

    def _wait_while_paused(self) -> bool:
        """Block while paused. Returns False if playback was stopped."""
        while True:
            if self._should_quit():
                return False
            with self._lock:
                paused = self._paused
            if not paused:
                return True
            time.sleep(0.02)

    # ---------------------------------------------------- audio worker

    def _audio_worker(self) -> None:
        """Decode + resample audio and push it to the sound device."""
        try:
            self._run_audio_worker()
        except Exception:
            # Audio failure must not crash the app; video keeps going.
            with self._lock:
                self._audio_ready = False
        finally:
            self._close_audio_stream()

    def _run_audio_worker(self) -> None:
        resampler = av.AudioResampler(
            format="s16", layout="stereo", rate=_AUDIO_SAMPLE_RATE
        )
        container = av.open(str(self.info.path), mode="r")
        try:
            stream = container.streams.audio[0]
            stream.thread_type = "AUTO"

            self._open_audio_stream()

            # Bug 2: content decoded between the seek keyframe and the
            # seek target is dropped instead of played (no stale burst).
            drop_until: Optional[float] = None

            while not self._should_quit():
                if not self._wait_while_paused():
                    break

                seek_target = self._consume_seek("_audio_seek_to")
                if seek_target is not None:
                    self._flush_audio_stream()
                    # Bug 2: with ``stream=`` the offset is in STREAM
                    # time_base units, not AV_TIME_BASE microseconds
                    # (``av.time_base`` is 1000000). The old
                    # ``int(target * av.time_base)`` seeked ~1000x past
                    # EOF, so the stream ended instantly after a seek.
                    container.seek(
                        int(seek_target / float(stream.time_base)),
                        stream=stream,
                        any_frame=False,
                    )
                    with self._lock:
                        self._audio_clock = seek_target
                        self._audio_ready = True
                    drop_until = seek_target

                packet = next(container.demux(stream), None)
                if packet is None:
                    break  # end of audio stream
                if packet.dts is None and packet.size == 0:
                    break  # end-of-stream flush packet

                try:
                    frames = packet.decode()
                except Exception:
                    continue  # skip a corrupt packet, keep playing

                for frame in frames:
                    if self._should_quit():
                        return
                    if not self._wait_while_paused():
                        return
                    with self._lock:
                        seek_pending = self._audio_seek_to is not None
                    if seek_pending:
                        break  # handle the new seek in the outer loop

                    # Bug 2: drop frames that end before the seek target.
                    if drop_until is not None:
                        frame_end = None
                        if frame.pts is not None:
                            frame_end = float(
                                frame.pts * frame.time_base
                            ) + float(frame.samples) / float(
                                frame.sample_rate
                            )
                        if frame_end is not None and frame_end <= drop_until:
                            continue  # pre-target: drop, clock unchanged
                        drop_until = None

                    # Wait until the video clock has nearly caught up
                    # with this audio chunk (keeps A/V in sync).
                    while not self._should_quit():
                        with self._lock:
                            paused = self._paused
                            video_clock = self._position
                            audio_clock = self._audio_clock
                            seek_pending = self._audio_seek_to is not None
                        if paused or seek_pending:
                            break
                        if audio_clock - video_clock < _MAX_AV_DRIFT:
                            break
                        time.sleep(0.005)
                    if self._should_quit():
                        return
                    with self._lock:
                        seek_pending = self._audio_seek_to is not None
                    if seek_pending:
                        # Bug 2 (RC4): a seek arrived while this pre-seek
                        # frame was waiting for the video clock. Drop it
                        # without writing or advancing the audio clock;
                        # the seek is handled in the outer loop.
                        break

                    try:
                        resampled_list = resampler.resample(frame)
                    except Exception:
                        continue
                    for resampled in resampled_list:
                        data = resampled.to_ndarray().reshape(-1, 2)
                        gain = self._current_gain()
                        if gain != 1.0:
                            data = (data.astype(np.float32) * gain).astype(
                                np.int16
                            )
                        self._write_audio(data.tobytes())
                        with self._lock:
                            self._audio_clock += float(
                                resampled.samples
                            ) / float(_AUDIO_SAMPLE_RATE)
                            self._audio_ready = True
        finally:
            container.close()

    def _open_audio_stream(self) -> None:
        if sd is None:
            return
        try:
            self._audio_stream = sd.RawOutputStream(
                samplerate=_AUDIO_SAMPLE_RATE,
                channels=2,
                dtype="int16",
                blocksize=_AUDIO_BLOCK_SIZE,
            )
        except Exception:
            # No usable audio device: continue silently without audio.
            self._audio_stream = None

    def _close_audio_stream(self) -> None:
        stream, self._audio_stream = self._audio_stream, None
        if stream is not None:
            try:
                # Bug 2 (RC5): abort() discards pending buffers; stop()
                # would wait for them to play out (audible tail after
                # stop/close).
                stream.abort()
                stream.close()
            except Exception:
                pass

    def _flush_audio_stream(self) -> None:
        stream = self._audio_stream
        if stream is not None:
            try:
                # Bug 2 (RC5): abort() drops the already-buffered audio
                # immediately; stop() would drain it first (tail keeps
                # playing after pause/seek).
                stream.abort()
                stream.start()
            except Exception:
                pass

    def _write_audio(self, data: bytes) -> None:
        stream = self._audio_stream
        chunk_seconds = len(data) / (4 * _AUDIO_SAMPLE_RATE)
        if stream is None:
            # No audio output available: advance the clock in real time
            # so the video pacing stays correct.
            time.sleep(chunk_seconds)
            return
        try:
            stream.write(data)
        except Exception:
            # Device glitch: pace in real time instead of racing ahead.
            time.sleep(chunk_seconds)

    # ---------------------------------------------------- video worker

    def _video_worker(self) -> None:
        try:
            self._run_video_worker()
            finished = False
            with self._lock:
                if self._playing and not self._stop_requested:
                    finished = True
                    self._playing = False
                    self._position = self.duration
            if finished and self._on_finished is not None:
                self._on_finished()
        except Exception:
            with self._lock:
                self._playing = False
            if self._on_error is not None:
                self._on_error(
                    "Video playback failed. The file may be corrupt."
                )

    def _run_video_worker(self) -> None:
        container = av.open(str(self.info.path), mode="r")
        try:
            stream = container.streams.video[0]
            # Bug 2: do NOT use frame threading ("AUTO") on this stream.
            # After container.seek() the frame-threaded decoder reports
            # end-of-stream after only a few frames (probe: seek to 1.5 s
            # yielded 3 frames with AUTO vs 15 with NONE), which killed
            # the video worker and snapped playback to the end after
            # every timeline seek. Single-threaded decode is fast enough
            # for preview playback.
            stream.thread_type = "NONE"
            time_base = float(stream.time_base)

            last_tick = 0.0
            # Bug 2: frames decoded between the seek keyframe and the
            # seek target are dropped instead of shown (no stale flash).
            drop_until: Optional[float] = None
            while not self._should_quit():
                if not self._wait_while_paused():
                    break

                seek_target = self._consume_seek("_video_seek_to")
                if seek_target is not None:
                    # Bug 2: with ``stream=`` the offset is in STREAM
                    # time_base units, not AV_TIME_BASE microseconds
                    # (``av.time_base`` is 1000000). The old
                    # ``int(target * av.time_base)`` seeked ~1000x past
                    # EOF, so the stream ended instantly after a seek.
                    container.seek(
                        int(seek_target / float(stream.time_base)),
                        stream=stream,
                        any_frame=False,
                    )
                    with self._lock:
                        self._position = seek_target
                        self._audio_clock = seek_target
                        self._video_start_wall = None
                        self._video_start_pts = None
                    drop_until = seek_target

                frame = self._decode_next_frame(container, stream)
                if frame is None:
                    break  # end of video stream

                pts = frame.pts
                frame_time = (
                    float(pts * time_base) if pts is not None else None
                )

                # Bug 2: drop frames that end before the seek target.
                if drop_until is not None and frame_time is not None:
                    if frame_time < drop_until:
                        continue  # pre-target: skip, position unchanged
                    drop_until = None

                # Sync: wait until the reference clock (audio clock, or
                # wall clock when there is no audio) reaches this
                # frame's timestamp.
                if frame_time is not None:
                    if not self._wait_for_frame_time(frame_time):
                        break
                    with self._lock:
                        if self._paused or self._should_quit():
                            continue
                        if self._video_seek_to is not None:
                            # Bug 2 (RC4): a seek arrived while this
                            # pre-seek frame was waiting to be shown.
                            # Drop it (do not update the position); the
                            # seek is handled at the top of the loop.
                            continue
                        self._position = frame_time

                image = frame.to_image()  # PIL RGB image
                if self._on_frame is not None:
                    self._on_frame(image)

                if (
                    self._on_tick is not None
                    and time.monotonic() - last_tick >= 0.1
                ):
                    last_tick = time.monotonic()
                    with self._lock:
                        position = self._position
                    self._on_tick(position, self.duration)
        finally:
            container.close()

    def _wait_for_frame_time(self, frame_time: float) -> bool:
        """Sleep until it is time to show this frame.

        Returns False when playback was stopped while waiting.
        """
        while not self._should_quit():
            with self._lock:
                paused = self._paused
                audio_ready = self._audio_ready
                audio_clock = self._audio_clock
                start_wall = self._video_start_wall
                start_pts = self._video_start_pts
            if paused:
                return True  # caller re-checks pause state

            if audio_ready:
                delay = frame_time - audio_clock
            else:
                # No audio: pace against wall time from the first frame.
                now_wall = time.monotonic()
                if start_wall is None or start_pts is None:
                    with self._lock:
                        self._video_start_wall = now_wall
                        self._video_start_pts = frame_time
                    delay = 0.0
                else:
                    delay = frame_time - (
                        start_pts + (now_wall - start_wall)
                    )

            if delay <= 0:
                return True
            time.sleep(min(delay, 0.02))
        return False

    def _decode_next_frame(self, container, stream):
        """Decode the next video frame, handling flush packets."""
        for packet in container.demux(stream):
            if self._should_quit():
                return None
            if packet.size == 0 and packet.dts is None:
                return None  # end-of-stream flush packet
            try:
                for frame in packet.decode():
                    return frame
            except Exception:
                continue  # skip a corrupt packet, keep playing
        return None
