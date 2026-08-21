"""Timeline data model (Phases 5–7).

This module is deliberately UI-free: it contains only the timeline
state and pure time<->pixel scaling math. No Tkinter, no decoding, no
file access. The source video file is referenced by path only and is
NEVER copied, modified or re-encoded by the timeline.

Scope (Phases 5–7):
- data model: VideoClip, AudioClip, Timeline
- timeline duration (== active video duration, 0 when no video)
- audio clip placement with bounds clamping (Phase 7): a clip's start
  always stays within [0, duration] so clips never over-run the
  timeline; clips longer than the timeline pin to start 0
- playhead position (seconds, clamped to the timeline)
- time ruler tick selection
- time <-> pixel scaling helpers
- WAV duration probing via stdlib ``wave`` (Phase 6)

Explicitly NOT implemented here (future phases): trimming, splitting,
snapping, transitions, effects, mixing, export, project save/load.
"""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

#: Candidate ruler tick steps, in seconds (from dense to sparse).
_TICK_STEP_CANDIDATES = (
    0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0,
    120.0, 300.0, 600.0, 1200.0, 3600.0,
)

#: Maximum number of ruler intervals we want across the timeline.
_MAX_TICK_INTERVALS = 6


@dataclass
class VideoClip:
    """A non-destructive reference to the active source video.

    ``start`` is the position of the clip on the timeline in seconds
    (0 for now — trimming/splitting arrive in a future phase).
    """

    source: Path
    start: float
    duration: float


@dataclass
class AudioClip:
    """A non-destructive reference to an audio file (future phase).

    Audio clips are part of the model already so the timeline state is
    complete, but nothing places them on the timeline yet.
    """

    source: Path
    start: float
    duration: float


@dataclass
class Timeline:
    """Timeline state: one video clip, (empty) audio clips, playhead."""

    video_clip: Optional[VideoClip] = None
    audio_clips: List[AudioClip] = field(default_factory=list)
    playhead: float = 0.0  # seconds
    selected_audio: Optional[int] = None  # index into audio_clips

    # ------------------------------------------------------------- state

    @property
    def duration(self) -> float:
        """Timeline duration == active video duration (0 if none)."""
        if self.video_clip is None:
            return 0.0
        return max(0.0, self.video_clip.duration)

    def set_video(self, source: Path, duration: float) -> None:
        """Attach the active source video as the timeline's video clip.

        Only metadata is stored; the file itself is never touched.
        The playhead resets to the start of the timeline. Existing
        audio clips are kept but re-clamped into the new bounds
        (Phase 7: a shorter video must not leave clips over-running).
        """
        self.video_clip = VideoClip(
            source=Path(source),
            start=0.0,
            duration=max(0.0, float(duration)),
        )
        self.playhead = 0.0
        self.clamp_audio_clips()

    def clear_video(self) -> None:
        """Remove the video clip (empty timeline, duration 0).

        Audio clips are kept (Phase 7 policy) but pinned to start 0
        while the timeline has no duration.
        """
        self.video_clip = None
        self.playhead = 0.0
        self.clamp_audio_clips()

    # ------------------------------------------------------------ audio

    def _clamp_start(self, start: float, clip_duration: float) -> float:
        """Clamp a clip start into the timeline bounds (Phase 7).

        - ``start`` is always >= 0.
        - With a positive timeline duration the clip's start is also
          capped so the clip never over-runs the right edge:
          ``start <= duration - clip_duration``.
        - A clip longer than the timeline pins to start 0 (it may
          visually extend; trimming is a future phase).
        - With no timeline duration (no video) the start pins to 0.
        """
        start = max(0.0, float(start))
        duration = self.duration
        if duration <= 0.0:
            return 0.0
        latest = duration - max(0.0, float(clip_duration))
        return min(start, max(0.0, latest))

    def add_audio_clip(self, source: Path, start: float, duration: float) -> AudioClip:
        """Create and append a new audio clip at ``start`` (seconds).

        Only a reference/path is stored; the WAV file is never touched.
        ``start`` is clamped into the timeline bounds (Phase 7): never
        negative, and never so far right that the clip over-runs the
        timeline. Returns the new clip.
        """
        clip = AudioClip(
            source=Path(source),
            start=self._clamp_start(start, duration),
            duration=max(0.0, float(duration)),
        )
        self.audio_clips.append(clip)
        return clip

    def move_audio_clip(self, index: int, start: float) -> None:
        """Move an existing audio clip horizontally (change only its start).

        The clip's source and duration are intentionally left untouched.
        ``start`` is clamped into the timeline bounds (Phase 7), so a
        drag past the right edge stops at ``duration - clip.duration``.
        """
        if index < 0 or index >= len(self.audio_clips):
            return
        clip = self.audio_clips[index]
        clip.start = self._clamp_start(start, clip.duration)

    def clamp_audio_clips(self) -> None:
        """Re-clamp every audio clip into the current timeline bounds.

        Called when the video duration changes (new video / cleared
        video) so existing clips never over-run the timeline. Clips are
        kept, never deleted (Phase 7 policy).
        """
        for clip in self.audio_clips:
            clip.start = self._clamp_start(clip.start, clip.duration)

    def select_audio(self, index: Optional[int]) -> None:
        """Select (or deselect, with None) an audio clip by index."""
        if index is not None and (index < 0 or index >= len(self.audio_clips)):
            self.selected_audio = None
            return
        self.selected_audio = index

    def audio_clip_at(self, seconds: float, top: float, bottom: float) -> Optional[int]:
        """Return the index of the audio clip whose horizontal time range
        covers ``seconds``, or None if none matches.

        Overlapping clips resolve to the topmost one: the last-added
        clip is drawn on top, so it is checked first (Phase 7).

        ``top``/``bottom`` are passed by the caller only to document the
        intended hit-test contract; the y-coordinate check is performed by
        the UI layer so the model stays pixel-free.
        """
        for index in range(len(self.audio_clips) - 1, -1, -1):
            clip = self.audio_clips[index]
            clip_end = clip.start + clip.duration
            if clip.start <= seconds <= clip_end:
                return index
        return None

    def set_playhead(self, seconds: float) -> float:
        """Move the playhead, clamped to [0, duration]. Returns it."""
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds < 0.0 or math.isnan(seconds):
            seconds = 0.0
        self.playhead = min(seconds, self.duration)
        return self.playhead

    # ----------------------------------------------------------- scaling

    def time_to_x(self, seconds: float, left: float, width: float) -> float:
        """Map a time (seconds) to a horizontal pixel position.

        ``left`` is the pixel x of time 0 and ``width`` the pixel width
        of the whole timeline. Zero/negative duration maps everything
        to the left edge so drawing never divides by zero.
        """
        duration = self.duration
        if duration <= 0.0 or width <= 0.0:
            return float(left)
        fraction = max(0.0, min(float(seconds) / duration, 1.0))
        return float(left) + fraction * float(width)

    def x_to_time(self, x: float, left: float, width: float) -> float:
        """Map a horizontal pixel position back to a time in seconds.

        The result is clamped to [0, duration]. Clicks outside the
        timeline area therefore seek to the nearest edge.
        """
        duration = self.duration
        if duration <= 0.0 or width <= 0.0:
            return 0.0
        fraction = (float(x) - float(left)) / float(width)
        fraction = max(0.0, min(fraction, 1.0))
        return fraction * duration

    # ------------------------------------------------------------- ruler

    def ruler_tick_times(self) -> List[float]:
        """Times (seconds) where ruler ticks should be drawn.

        Always includes 0. Tick spacing is chosen from a fixed set of
        sensible steps so the ruler stays readable for very short and
        very long videos alike. Empty timeline -> a single 0 tick.
        """
        duration = self.duration
        if duration <= 0.0:
            return [0.0]
        step = _pick_tick_step(duration)
        ticks: List[float] = []
        t = 0.0
        # +epsilon so the final tick at exactly ``duration`` is included.
        while t <= duration + 1e-6:
            ticks.append(round(t, 3))
            t += step
        return ticks


def _pick_tick_step(duration: float) -> float:
    """Choose the smallest candidate step with at most ~6 intervals."""
    for step in _TICK_STEP_CANDIDATES:
        if duration / step <= _MAX_TICK_INTERVALS:
            return step
    # Extremely long videos: round up to whole minutes per interval.
    return float(math.ceil(duration / _MAX_TICK_INTERVALS / 60.0) * 60.0)


def format_ruler_label(seconds: float) -> str:
    """Human-readable ruler label: ``0s``, ``5s``, ``0.5s``, ``1m30s``."""
    if seconds <= 0.0:
        return "0s"
    if seconds < 1.0:
        return f"{seconds:.1f}s"
    if seconds < 60.0:
        whole = int(round(seconds))
        return f"{whole}s"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if secs == 0:
        return f"{minutes}m"
    return f"{minutes}m{secs:02d}s"


def probe_wav_duration(wav_path: Path) -> float:
    """Read a WAV file's duration in seconds (frames / frame rate).

    Phase 6 needs the real duration so an AudioClip placed on the timeline
    is the correct length. Pure stdlib ``wave``: no subprocess, no FFmpeg.
    Raises OSError (or wave.Error) when the file is missing or not a WAV.
    """
    with wave.open(str(Path(wav_path)), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        frames = wav_file.getnframes()
    if frame_rate <= 0:
        return 0.0
    return float(frames) / float(frame_rate)
