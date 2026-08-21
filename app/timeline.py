"""Timeline data model (Phase 5 — timeline foundation).

This module is deliberately UI-free: it contains only the timeline
state and pure time<->pixel scaling math. No Tkinter, no decoding, no
file access. The source video file is referenced by path only and is
NEVER copied, modified or re-encoded by the timeline.

Scope of this phase:
- data model: VideoClip, AudioClip, Timeline
- timeline duration (== active video duration, 0 when no video)
- playhead position (seconds, clamped to the timeline)
- time ruler tick selection
- time <-> pixel scaling helpers

Explicitly NOT implemented here (future phases): drag and drop, audio
placement, trimming, splitting, resizing, snapping, transitions,
effects, mixing, export, project save/load.
"""

from __future__ import annotations

import math
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
        The playhead resets to the start of the timeline.
        """
        self.video_clip = VideoClip(
            source=Path(source),
            start=0.0,
            duration=max(0.0, float(duration)),
        )
        self.playhead = 0.0

    def clear_video(self) -> None:
        """Remove the video clip (empty timeline, duration 0)."""
        self.video_clip = None
        self.playhead = 0.0

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
