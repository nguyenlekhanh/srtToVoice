"""UI interaction tests for the timeline (Phase 7).

These tests instantiate the real ``App`` (Tkinter) and drive it with
synthetic events — the same technique used in Phase 6 smoke tests.
No video decoding, no Piper, no subprocess: the timeline is driven
purely through the model + canvas event handlers.

Run from the project root:
    .venv\\Scripts\\python.exe -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from app.main import App


def write_wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    """Write a tiny silent mono WAV of exactly ``seconds`` length."""
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


class TimelineAppTestCase(unittest.TestCase):
    """Base: create/destroy a real App around each test."""

    def setUp(self):
        self.app = App()
        self.app.update_idletasks()

    def tearDown(self):
        try:
            self.app.after_cancel(self.app._poll_after_id)
        except Exception:
            pass
        self.app.destroy()

    # -- helpers ----------------------------------------------------------

    def load_video(self, duration: float = 10.0) -> None:
        """Attach a fake video to the timeline (no real decoding)."""
        self.app.timeline.set_video(Path("fake_video.mp4"), duration)
        self.app._redraw_timeline()

    def geometry(self) -> dict:
        return self.app._timeline_geometry()

    def canvas_event(self, x: float, y: float) -> SimpleNamespace:
        """Fake event with canvas-local x/y (for Button-1 on the canvas)."""
        return SimpleNamespace(x=x, y=y)

    def root_event(self, canvas_x: float, canvas_y: float) -> SimpleNamespace:
        """Fake event with x_root/y_root mapping to canvas coords."""
        canvas = self.app.timeline_canvas
        return SimpleNamespace(
            x_root=canvas.winfo_rootx() + canvas_x,
            y_root=canvas.winfo_rooty() + canvas_y,
        )

    def audio_mid_y(self) -> float:
        g = self.geometry()
        return (g["audio_top"] + g["audio_bottom"]) / 2.0

    def time_to_canvas_x(self, seconds: float) -> float:
        g = self.geometry()
        return self.app.timeline.time_to_x(seconds, g["left"], g["width"])

    def status(self) -> str:
        return self.app.status_var.get()



class DropClipTests(TimelineAppTestCase):
    """Drag a WAV asset from the panel and drop it on the AUDIO track."""

    def _drop(self, wav_path: Path, seconds: float) -> None:
        """Simulate: press asset -> release over AUDIO track at ``seconds``."""
        self.app._on_asset_drag_start(wav_path)
        g = self.geometry()
        x = self.time_to_canvas_x(seconds)
        y = self.audio_mid_y()
        self.app._on_global_drag_release(self.root_event(x, y))

    def test_drop_at_start(self):
        self.load_video(10.0)
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "a.wav", 2.0)
            self._drop(wav, 0.0)
        self.assertEqual(len(self.app.timeline.audio_clips), 1)
        self.assertAlmostEqual(self.app.timeline.audio_clips[0].start, 0.0)
        self.assertIn("Added audio clip", self.status())

    def test_drop_in_middle(self):
        self.load_video(10.0)
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "a.wav", 2.0)
            self._drop(wav, 4.0)
        clip = self.app.timeline.audio_clips[0]
        self.assertAlmostEqual(clip.start, 4.0, places=1)

    def test_drop_near_end_is_clamped(self):
        self.load_video(10.0)
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "a.wav", 3.0)
            self._drop(wav, 9.5)  # would over-run -> clamp to 7.0
        clip = self.app.timeline.audio_clips[0]
        self.assertAlmostEqual(clip.start, 7.0, places=1)
        self.assertIn("clamped", self.status())

    def test_drop_overlong_wav_is_rejected(self):
        self.load_video(10.0)
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "long.wav", 15.0)
            self._drop(wav, 1.0)
        self.assertEqual(len(self.app.timeline.audio_clips), 0)
        self.assertIn("longer than the", self.status())

    def test_drop_outside_audio_track_cancels(self):
        self.load_video(10.0)
        with tempfile.TemporaryDirectory() as tmp:
            wav = write_wav(Path(tmp) / "a.wav", 2.0)
            self.app._on_asset_drag_start(wav)
            g = self.geometry()
            x = self.time_to_canvas_x(2.0)
            y = g["video_top"] + 5  # over the VIDEO track, not AUDIO
            self.app._on_global_drag_release(self.root_event(x, y))
        self.assertEqual(len(self.app.timeline.audio_clips), 0)
        self.assertIn("cancelled", self.status())

    def test_multiple_clips_can_be_added(self):
        self.load_video(10.0)
        with tempfile.TemporaryDirectory() as tmp:
            self._drop(write_wav(Path(tmp) / "a.wav", 1.0), 1.0)
            self._drop(write_wav(Path(tmp) / "b.wav", 1.0), 5.0)
        self.assertEqual(len(self.app.timeline.audio_clips), 2)


class DragClipTests(TimelineAppTestCase):
    """Press an existing clip and drag it; the model must clamp it."""

    def _add_clip(self, start: float, duration: float) -> None:
        self.app.timeline.add_audio_clip(Path("clip.wav"), start, duration)
        self.app._redraw_timeline()

    def _drag_clip(self, from_seconds: float, to_seconds: float) -> None:
        """Press just inside the clip's left edge, release at ``to_seconds``.

        Pressing slightly inside the clip (instead of on the exact edge)
        avoids float round-trip misses in the hit test; the resulting
        0.05 s grab offset is absorbed by the tests' delta tolerance.
        """
        g = self.geometry()
        y = self.audio_mid_y()
        press_x = self.time_to_canvas_x(from_seconds + 0.05)
        self.app._on_timeline_click(self.canvas_event(press_x, y))
        # Motion to the destination (live move).
        move_x = self.time_to_canvas_x(to_seconds)
        self.app._on_global_drag_motion(self.root_event(move_x, y))
        self.app._on_global_drag_release(self.root_event(move_x, y))

    def test_drag_to_middle(self):
        self.load_video(10.0)
        self._add_clip(1.0, 2.0)
        self._drag_clip(1.0, 5.0)
        self.assertAlmostEqual(self.app.timeline.audio_clips[0].start, 5.0, delta=0.2)

    def test_drag_past_right_edge_clamps(self):
        self.load_video(10.0)
        self._add_clip(1.0, 3.0)
        self._drag_clip(1.0, 9.9)  # would over-run -> clamp to 7.0
        self.assertAlmostEqual(self.app.timeline.audio_clips[0].start, 7.0, delta=0.2)

    def test_drag_past_left_edge_clamps_to_zero(self):
        self.load_video(10.0)
        self._add_clip(4.0, 2.0)
        self._drag_clip(4.0, 0.0)
        self.assertAlmostEqual(self.app.timeline.audio_clips[0].start, 0.0, delta=0.2)

    def test_drag_selects_the_clip(self):
        self.load_video(10.0)
        self._add_clip(2.0, 2.0)
        g = self.geometry()
        y = self.audio_mid_y()
        x = self.time_to_canvas_x(2.5)
        self.app._on_timeline_click(self.canvas_event(x, y))
        self.assertEqual(self.app.timeline.selected_audio, 0)
        self.assertIn("Selected audio clip", self.status())

    def test_release_reports_moved_position(self):
        self.load_video(10.0)
        self._add_clip(1.0, 2.0)
        self._drag_clip(1.0, 5.0)
        self.assertIn("Audio clip moved to", self.status())


class SelectionTests(TimelineAppTestCase):
    def test_click_empty_audio_track_deselects(self):
        self.load_video(10.0)
        self.app.timeline.add_audio_clip(Path("a.wav"), 1.0, 2.0)
        self.app.timeline.select_audio(0)
        self.app._redraw_timeline()
        # Click on the AUDIO track where there is no clip (t = 6s).
        g = self.geometry()
        y = self.audio_mid_y()
        x = self.time_to_canvas_x(6.0)
        self.app._on_timeline_click(self.canvas_event(x, y))
        self.assertIsNone(self.app.timeline.selected_audio)

    def test_click_ruler_deselects_and_seeks(self):
        self.load_video(10.0)
        self.app.timeline.add_audio_clip(Path("a.wav"), 1.0, 2.0)
        self.app.timeline.select_audio(0)
        g = self.geometry()
        y = (g["ruler_top"] + g["ruler_bottom"]) / 2.0
        x = self.time_to_canvas_x(5.0)
        self.app._on_timeline_click(self.canvas_event(x, y))
        self.assertIsNone(self.app.timeline.selected_audio)
        self.assertAlmostEqual(self.app.timeline.playhead, 5.0, delta=0.2)

    def test_click_video_track_deselects(self):
        self.load_video(10.0)
        self.app.timeline.add_audio_clip(Path("a.wav"), 1.0, 2.0)
        self.app.timeline.select_audio(0)
        g = self.geometry()
        y = (g["video_top"] + g["video_bottom"]) / 2.0
        x = self.time_to_canvas_x(5.0)
        self.app._on_timeline_click(self.canvas_event(x, y))
        self.assertIsNone(self.app.timeline.selected_audio)

    def test_selected_clip_drawn_with_selected_colors(self):
        self.load_video(10.0)
        self.app.timeline.add_audio_clip(Path("a.wav"), 1.0, 2.0)
        self.app.timeline.select_audio(0)
        self.app._redraw_timeline()
        from app.main import AUDIO_CLIP_SELECTED_FILL

        canvas = self.app.timeline_canvas
        fills = [
            canvas.itemcget(item, "fill")
            for item in canvas.find_withtag("audio_clip")
            if canvas.type(item) == "rectangle"
        ]
        self.assertIn(AUDIO_CLIP_SELECTED_FILL, fills)

    def test_deselected_clip_uses_normal_fill(self):
        self.load_video(10.0)
        self.app.timeline.add_audio_clip(Path("a.wav"), 1.0, 2.0)
        self.app._redraw_timeline()
        from app.main import AUDIO_CLIP_FILL, AUDIO_CLIP_SELECTED_FILL

        canvas = self.app.timeline_canvas
        fills = [
            canvas.itemcget(item, "fill")
            for item in canvas.find_withtag("audio_clip")
            if canvas.type(item) == "rectangle"
        ]
        self.assertIn(AUDIO_CLIP_FILL, fills)
        self.assertNotIn(AUDIO_CLIP_SELECTED_FILL, fills)


class SeekTests(TimelineAppTestCase):
    def test_click_timeline_seeks_playhead(self):
        self.load_video(10.0)
        g = self.geometry()
        y = self.audio_mid_y()
        x = self.time_to_canvas_x(3.0)
        self.app._on_timeline_click(self.canvas_event(x, y))
        self.assertAlmostEqual(self.app.timeline.playhead, 3.0, delta=0.2)
        self.assertIn("Timeline seeked to", self.status())

    def test_click_beyond_right_edge_seeks_to_end(self):
        self.load_video(10.0)
        g = self.geometry()
        y = self.audio_mid_y()
        x = g["left"] + g["width"] + 50  # outside the drawing extent
        self.app._on_timeline_click(self.canvas_event(x, y))
        self.assertAlmostEqual(self.app.timeline.playhead, 10.0, delta=0.01)

    def test_click_on_empty_timeline_does_not_seek(self):
        # No video loaded: duration 0 -> clicks must not move the playhead.
        g = self.geometry()
        y = self.audio_mid_y()
        x = self.time_to_canvas_x(3.0)
        self.app._on_timeline_click(self.canvas_event(x, y))
        self.assertEqual(self.app.timeline.playhead, 0.0)

    def test_video_tick_mirrors_playhead(self):
        self.load_video(10.0)
        # Simulate a VideoPlayer tick (no real player involved).
        self.app._apply_video_tick(self.app._video_tick_token, 4.5, 10.0)
        self.assertAlmostEqual(self.app.timeline.playhead, 4.5)

    def test_stale_video_tick_is_ignored(self):
        self.load_video(10.0)
        stale_token = self.app._video_tick_token - 1
        self.app._apply_video_tick(stale_token, 9.9, 10.0)
        self.assertEqual(self.app.timeline.playhead, 0.0)


if __name__ == "__main__":
    unittest.main()


