"""Temporary Bug 2 RC6 probe: stale UI ticks during the seek-pending window.

Drives the REAL App (Tkinter) exactly like tests/test_timeline_ui.py:
fake video on the timeline + fake player, no decoding. ``app.after`` /
``after_cancel`` are replaced with a deterministic capture so timer
firing does not depend on wall-clock timing. Reproduces:
1. A pre-seek worker tick moving the time label + playhead BACK during
   the 150 ms seek-pending window (only the slider var is suppressed).
2. Untracked after() timers: two rapid seeks leave TWO clear-timers;
   the first one clears the pending flag ~100 ms before the second
   seek's window ends, so stale ticks slip through again.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.main import App


class FakePlayer:
    def __init__(self, duration: float):
        self.duration = duration
        self.seeks: list[float] = []
        self.is_active = False

    def seek(self, seconds: float) -> None:
        self.seeks.append(seconds)

    def show_first_frame(self) -> None:
        pass


def main() -> None:
    app = App()
    app.update_idletasks()
    app.timeline.set_video(Path("fake.mp4"), 10.0)
    app._redraw_timeline()
    app.video_player = FakePlayer(10.0)

    # Deterministic timer capture (insertion-ordered).
    timers: "dict[str, object]" = {}
    counter = [0]

    def fake_after(ms, func=None, *args):
        counter[0] += 1
        tid = f"t{counter[0]}"
        timers[tid] = func
        return tid

    def fake_cancel(tid):
        timers.pop(tid, None)

    app.after = fake_after
    app.after_cancel = fake_cancel

    g = app._timeline_geometry()
    y = (g["audio_top"] + g["audio_bottom"]) / 2.0

    def click(seconds: float) -> None:
        x = app.timeline.time_to_x(seconds, g["left"], g["width"])
        app._on_timeline_click(SimpleNamespace(x=x, y=y))

    def tick(pos: float) -> None:
        app._apply_video_tick(app._video_tick_token, pos, 10.0)

    def fire_one_timer() -> None:
        tid = next(iter(timers))
        func = timers.pop(tid)
        func()

    # Part 1: playing at 1.0 s; user clicks the timeline at 5.0 --------
    tick(1.0)
    click(5.0)
    # A worker tick from BEFORE the seek arrives inside the 150 ms window.
    tick(1.05)
    playhead = app.timeline.playhead
    label = app.time_label.cget("text")
    stale_moved_ui = playhead < 4.5 or not label.startswith("00:05")
    print(f"[{'BAD' if stale_moved_ui else 'OK '}] RC6 stale tick during "
          f"seek-pending: playhead={playhead:.2f} label={label!r} "
          f"(expected to stay at ~5.00 / '00:05 ...')")

    # Let the Part-1 settle window end (its clear-timer fires).
    while timers:
        fire_one_timer()

    # Part 2: untracked timers on rapid double-seek ---------------------
    click(5.0)
    click(7.0)
    n_timers = len(timers)  # 2 pre-fix (untracked), 1 post-fix (tracked)
    # Stale pre-seek tick while the LAST seek is still settling.
    tick(1.1)
    playhead2 = app.timeline.playhead
    stale_applied = playhead2 < 6.5
    early_clear_bad = n_timers > 1 or stale_applied
    print(f"[{'BAD' if early_clear_bad else 'OK '}] RC6 untracked timers: "
          f"pending_clear_timers={n_timers} playhead={playhead2:.2f} "
          f"(expected 1 timer and playhead ~7.00)")

    # After the settle window really ends, ticks must apply again.
    while timers:
        fire_one_timer()
    tick(7.2)
    resumed = app.timeline.playhead >= 7.0
    print(f"[{'OK ' if resumed else 'BAD'}] RC6 ticks resume after window: "
          f"pending={app._video_seek_pending} "
          f"playhead={app.timeline.playhead:.2f}")

    app.destroy()


if __name__ == "__main__":
    main()