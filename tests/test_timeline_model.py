"""Pure model tests for app.timeline (Phases 7–8).

No Tkinter, no UI: these tests exercise the timeline data model only —
bounds clamping, hit-testing, selection, playhead, scaling roundtrip,
ruler ticks, stdlib WAV duration probing and audio clip trimming.

Run from the project root:
    .venv\\Scripts\\python.exe -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from app.timeline import (
    MIN_AUDIO_CLIP_DURATION,
    Timeline,
    format_ruler_label,
    probe_wav_duration,
)
from app.video_preview import format_timecode

FAKE_VIDEO = Path("fake_video.mp4")
FAKE_WAV = Path("fake_audio.wav")


def make_timeline(duration: float = 10.0) -> Timeline:
    """Timeline with a video of ``duration`` seconds attached."""
    tl = Timeline()
    tl.set_video(FAKE_VIDEO, duration)
    return tl


def write_wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    """Write a tiny silent mono WAV of exactly ``seconds`` length."""
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


class TimelineStateTests(unittest.TestCase):
    def test_empty_timeline_has_zero_duration(self):
        tl = Timeline()
        self.assertEqual(tl.duration, 0.0)
        self.assertIsNone(tl.video_clip)
        self.assertEqual(tl.audio_clips, [])
        self.assertIsNone(tl.selected_audio)
        self.assertEqual(tl.playhead, 0.0)

    def test_set_video_stores_metadata_and_resets_playhead(self):
        tl = Timeline()
        tl.playhead = 5.0
        tl.set_video(FAKE_VIDEO, 12.5)
        self.assertEqual(tl.duration, 12.5)
        self.assertEqual(tl.video_clip.source, FAKE_VIDEO)
        self.assertEqual(tl.video_clip.start, 0.0)
        self.assertEqual(tl.video_clip.duration, 12.5)
        self.assertEqual(tl.playhead, 0.0)

    def test_set_video_rejects_negative_duration(self):
        tl = Timeline()
        tl.set_video(FAKE_VIDEO, -3.0)
        self.assertEqual(tl.duration, 0.0)

    def test_clear_video_keeps_audio_clips_pinned_to_zero(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 4.0, 2.0)
        tl.clear_video()
        self.assertIsNone(tl.video_clip)
        self.assertEqual(tl.duration, 0.0)
        self.assertEqual(len(tl.audio_clips), 1)
        self.assertEqual(tl.audio_clips[0].start, 0.0)
        self.assertEqual(tl.audio_clips[0].duration, 2.0)


class AudioClipBoundsTests(unittest.TestCase):
    def test_add_clip_inside_bounds_keeps_start(self):
        tl = make_timeline(10.0)
        clip = tl.add_audio_clip(FAKE_WAV, 2.0, 3.0)
        self.assertEqual(clip.start, 2.0)
        self.assertEqual(clip.duration, 3.0)
        self.assertEqual(len(tl.audio_clips), 1)

    def test_add_clip_negative_start_clamps_to_zero(self):
        tl = make_timeline(10.0)
        clip = tl.add_audio_clip(FAKE_WAV, -5.0, 2.0)
        self.assertEqual(clip.start, 0.0)

    def test_add_clip_overrunning_right_edge_is_clamped(self):
        tl = make_timeline(10.0)
        clip = tl.add_audio_clip(FAKE_WAV, 9.0, 3.0)
        self.assertEqual(clip.start, 7.0)  # 10 - 3
        self.assertEqual(clip.start + clip.duration, 10.0)

    def test_add_clip_exactly_at_end_is_allowed(self):
        tl = make_timeline(10.0)
        clip = tl.add_audio_clip(FAKE_WAV, 8.0, 2.0)
        self.assertEqual(clip.start, 8.0)

    def test_add_clip_longer_than_timeline_pins_to_zero(self):
        tl = make_timeline(10.0)
        clip = tl.add_audio_clip(FAKE_WAV, 6.0, 15.0)
        self.assertEqual(clip.start, 0.0)
        self.assertEqual(clip.duration, 15.0)

    def test_add_clip_on_empty_timeline_pins_to_zero(self):
        tl = Timeline()
        clip = tl.add_audio_clip(FAKE_WAV, 4.0, 2.0)
        self.assertEqual(clip.start, 0.0)

    def test_move_clip_clamps_both_edges(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, 3.0)
        tl.move_audio_clip(0, -4.0)
        self.assertEqual(tl.audio_clips[0].start, 0.0)
        tl.move_audio_clip(0, 9.5)
        self.assertEqual(tl.audio_clips[0].start, 7.0)  # 10 - 3
        tl.move_audio_clip(0, 5.0)
        self.assertEqual(tl.audio_clips[0].start, 5.0)

    def test_move_clip_keeps_source_and_duration(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 1.0, 2.5)
        tl.move_audio_clip(0, 4.0)
        clip = tl.audio_clips[0]
        self.assertEqual(clip.source, FAKE_WAV)
        self.assertEqual(clip.duration, 2.5)
        self.assertEqual(clip.start, 4.0)

    def test_move_clip_invalid_index_is_noop(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 1.0, 2.0)
        tl.move_audio_clip(-1, 5.0)
        tl.move_audio_clip(7, 5.0)
        self.assertEqual(tl.audio_clips[0].start, 1.0)


class AudioClipTrimTests(unittest.TestCase):
    """Phase 8: trimming only ever SHRINKS a clip, never extends it."""

    def test_trim_right_edge_moves_end_inward(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, 4.0)  # [2, 6]
        tl.trim_audio_clip(0, "right", 4.5)
        clip = tl.audio_clips[0]
        self.assertEqual(clip.start, 2.0)  # left edge fixed
        self.assertAlmostEqual(clip.duration, 2.5)  # end now 4.5

    def test_trim_left_edge_moves_start_inward(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, 4.0)  # [2, 6]
        tl.trim_audio_clip(0, "left", 3.5)
        clip = tl.audio_clips[0]
        self.assertAlmostEqual(clip.start, 3.5)
        self.assertAlmostEqual(clip.duration, 2.5)  # end stays at 6

    def test_trim_right_edge_outwards_never_extends(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, 4.0)
        tl.trim_audio_clip(0, "right", 9.0)  # past the original end
        clip = tl.audio_clips[0]
        self.assertEqual(clip.start, 2.0)
        self.assertEqual(clip.duration, 4.0)  # unchanged

    def test_trim_left_edge_outwards_never_extends(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, 4.0)
        tl.trim_audio_clip(0, "left", 0.0)  # before the original start
        clip = tl.audio_clips[0]
        self.assertEqual(clip.start, 2.0)  # unchanged
        self.assertEqual(clip.duration, 4.0)

    def test_trim_right_edge_respects_minimum_duration(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, 4.0)
        tl.trim_audio_clip(0, "right", 2.0)  # all the way to the start
        clip = tl.audio_clips[0]
        self.assertEqual(clip.start, 2.0)
        # Hard invariant: never below the minimum (float-exact).
        self.assertGreaterEqual(clip.duration, MIN_AUDIO_CLIP_DURATION)
        self.assertAlmostEqual(clip.duration, MIN_AUDIO_CLIP_DURATION)

    def test_trim_left_edge_respects_minimum_duration(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, 4.0)
        tl.trim_audio_clip(0, "left", 9.0)  # past the end
        clip = tl.audio_clips[0]
        self.assertAlmostEqual(clip.start, 6.0 - MIN_AUDIO_CLIP_DURATION)
        self.assertAlmostEqual(clip.duration, MIN_AUDIO_CLIP_DURATION)

    def test_trim_right_edge_of_overrunning_clip_caps_at_timeline(self):
        # Over-running clips cannot be created via the UI (Phase 7
        # rejects them on drop) but the model must handle them.
        tl = make_timeline(10.0)
        clip = tl.add_audio_clip(FAKE_WAV, 0.0, 12.0)
        tl.trim_audio_clip(0, "right", 11.0)
        self.assertEqual(clip.duration, 10.0)  # capped at timeline end
        tl.trim_audio_clip(0, "right", 7.0)
        self.assertEqual(clip.duration, 7.0)

    def test_trim_left_edge_of_overrunning_clip_keeps_phase7_invariant(self):
        tl = make_timeline(10.0)
        clip = tl.add_audio_clip(FAKE_WAV, 0.0, 12.0)
        tl.trim_audio_clip(0, "left", 11.0)
        # hi = min(12 - MIN, 10) = 10 -> start capped at the duration.
        self.assertEqual(clip.start, 10.0)
        self.assertAlmostEqual(clip.duration, 2.0)
        self.assertLessEqual(clip.start, tl.duration)

    def test_trim_keeps_source(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 1.0, 3.0)
        tl.trim_audio_clip(0, "right", 2.0)
        self.assertEqual(tl.audio_clips[0].source, FAKE_WAV)

    def test_trim_invalid_index_is_noop(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 1.0, 2.0)
        tl.trim_audio_clip(-1, "right", 2.0)
        tl.trim_audio_clip(5, "left", 2.0)
        self.assertEqual(tl.audio_clips[0].start, 1.0)
        self.assertEqual(tl.audio_clips[0].duration, 2.0)

    def test_trim_invalid_edge_is_noop(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 1.0, 2.0)
        tl.trim_audio_clip(0, "middle", 2.0)
        tl.trim_audio_clip(0, "", 2.0)
        self.assertEqual(tl.audio_clips[0].start, 1.0)
        self.assertEqual(tl.audio_clips[0].duration, 2.0)

    def test_trim_invalid_time_values_are_noops(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 1.0, 2.0)
        tl.trim_audio_clip(0, "right", float("nan"))
        tl.trim_audio_clip(0, "right", float("inf"))
        tl.trim_audio_clip(0, "left", float("-inf"))
        tl.trim_audio_clip(0, "left", "not a number")
        tl.trim_audio_clip(0, "left", None)
        self.assertEqual(tl.audio_clips[0].start, 1.0)
        self.assertEqual(tl.audio_clips[0].duration, 2.0)

    def test_trim_minimum_duration_clip_stays_at_minimum(self):
        # A clip already at the minimum cannot be shrunk further; the
        # result stays at the minimum (within float tolerance).
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, MIN_AUDIO_CLIP_DURATION)
        tl.trim_audio_clip(0, "right", 2.05)
        clip = tl.audio_clips[0]
        self.assertAlmostEqual(clip.start, 2.0)
        self.assertGreaterEqual(clip.duration, MIN_AUDIO_CLIP_DURATION)
        self.assertAlmostEqual(clip.duration, MIN_AUDIO_CLIP_DURATION)
        tl.trim_audio_clip(0, "left", 2.05)
        clip = tl.audio_clips[0]
        self.assertGreaterEqual(clip.duration, MIN_AUDIO_CLIP_DURATION)
        self.assertAlmostEqual(clip.duration, MIN_AUDIO_CLIP_DURATION)
        self.assertAlmostEqual(clip.start + clip.duration, 2.1)

    def test_trim_on_empty_timeline_still_shrinks(self):
        # No video -> timeline duration 0 -> no timeline cap, but the
        # clip can still be shrunk within its own range.
        tl = Timeline()
        tl.add_audio_clip(FAKE_WAV, 0.0, 2.0)
        tl.trim_audio_clip(0, "right", 1.0)
        self.assertEqual(tl.audio_clips[0].duration, 1.0)

    def test_trim_only_affects_target_clip(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 1.0, 2.0)
        tl.add_audio_clip(FAKE_WAV, 5.0, 2.0)
        tl.trim_audio_clip(1, "left", 6.0)
        self.assertEqual(tl.audio_clips[0].start, 1.0)
        self.assertEqual(tl.audio_clips[0].duration, 2.0)
        self.assertEqual(tl.audio_clips[1].start, 6.0)
        self.assertEqual(tl.audio_clips[1].duration, 1.0)


class ClampOnDurationChangeTests(unittest.TestCase):
    def test_shorter_video_clamps_existing_clips(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 8.0, 2.0)   # ends exactly at 10
        tl.add_audio_clip(FAKE_WAV, 1.0, 3.0)   # fits in the new bounds
        tl.set_video(Path("short.mp4"), 5.0)
        self.assertEqual(tl.audio_clips[0].start, 3.0)  # 5 - 2
        self.assertEqual(tl.audio_clips[1].start, 1.0)  # unchanged

    def test_longer_video_keeps_clip_positions(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 8.0, 2.0)
        tl.set_video(Path("long.mp4"), 60.0)
        self.assertEqual(tl.audio_clips[0].start, 8.0)

    def test_clip_longer_than_new_video_pins_to_zero(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 0.0, 9.0)
        tl.set_video(Path("short.mp4"), 4.0)
        self.assertEqual(tl.audio_clips[0].start, 0.0)
        self.assertEqual(tl.audio_clips[0].duration, 9.0)  # never trimmed

    def test_clips_are_never_deleted_by_duration_changes(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, 2.0)
        tl.add_audio_clip(FAKE_WAV, 6.0, 2.0)
        tl.set_video(Path("short.mp4"), 1.0)
        tl.clear_video()
        tl.set_video(Path("again.mp4"), 30.0)
        self.assertEqual(len(tl.audio_clips), 2)
        for clip in tl.audio_clips:
            self.assertGreaterEqual(clip.start, 0.0)
            self.assertLessEqual(clip.start, tl.duration)

    def test_clamp_audio_clips_is_idempotent(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 9.0, 3.0)
        tl.clamp_audio_clips()
        first = tl.audio_clips[0].start
        tl.clamp_audio_clips()
        self.assertEqual(tl.audio_clips[0].start, first)
        self.assertEqual(first, 7.0)


class HitTestAndSelectionTests(unittest.TestCase):
    def test_hit_inside_and_outside_clip(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 2.0, 3.0)  # covers [2, 5]
        self.assertEqual(tl.audio_clip_at(3.5, 0, 100), 0)
        self.assertEqual(tl.audio_clip_at(2.0, 0, 100), 0)   # left edge
        self.assertEqual(tl.audio_clip_at(5.0, 0, 100), 0)   # right edge
        self.assertIsNone(tl.audio_clip_at(1.9, 0, 100))
        self.assertIsNone(tl.audio_clip_at(5.1, 0, 100))

    def test_overlapping_clips_resolve_to_topmost(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(Path("a.wav"), 1.0, 4.0)  # index 0: [1, 5]
        tl.add_audio_clip(Path("b.wav"), 3.0, 4.0)  # index 1: [3, 7]
        self.assertEqual(tl.audio_clip_at(2.0, 0, 100), 0)
        self.assertEqual(tl.audio_clip_at(4.0, 0, 100), 1)  # overlap -> top
        self.assertEqual(tl.audio_clip_at(6.0, 0, 100), 1)

    def test_hit_test_on_empty_timeline(self):
        tl = Timeline()
        self.assertIsNone(tl.audio_clip_at(1.0, 0, 100))

    def test_select_and_deselect(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 1.0, 2.0)
        tl.select_audio(0)
        self.assertEqual(tl.selected_audio, 0)
        tl.select_audio(None)
        self.assertIsNone(tl.selected_audio)

    def test_select_invalid_index_deselects(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 1.0, 2.0)
        tl.select_audio(0)
        tl.select_audio(5)
        self.assertIsNone(tl.selected_audio)
        tl.select_audio(0)
        tl.select_audio(-1)
        self.assertIsNone(tl.selected_audio)


class PlayheadTests(unittest.TestCase):
    def test_playhead_clamped_to_bounds(self):
        tl = make_timeline(10.0)
        self.assertEqual(tl.set_playhead(4.0), 4.0)
        self.assertEqual(tl.set_playhead(-2.0), 0.0)
        self.assertEqual(tl.set_playhead(99.0), 10.0)

    def test_playhead_on_empty_timeline_pins_to_zero(self):
        tl = Timeline()
        self.assertEqual(tl.set_playhead(5.0), 0.0)

    def test_playhead_invalid_values_pin_to_zero(self):
        tl = make_timeline(10.0)
        self.assertEqual(tl.set_playhead(float("nan")), 0.0)
        self.assertEqual(tl.set_playhead("oops"), 0.0)
        self.assertEqual(tl.set_playhead(None), 0.0)

    def test_playhead_follows_duration_shrink(self):
        tl = make_timeline(10.0)
        tl.set_playhead(8.0)
        tl.set_video(Path("short.mp4"), 5.0)
        self.assertEqual(tl.playhead, 0.0)  # set_video resets it




class ScalingTests(unittest.TestCase):
    def test_time_x_roundtrip(self):
        tl = make_timeline(10.0)
        left, width = 56.0, 800.0
        for seconds in (0.0, 2.5, 5.0, 7.5, 10.0):
            x = tl.time_to_x(seconds, left, width)
            self.assertAlmostEqual(tl.x_to_time(x, left, width), seconds)

    def test_time_to_x_edges(self):
        tl = make_timeline(10.0)
        self.assertEqual(tl.time_to_x(0.0, 56, 800), 56.0)
        self.assertEqual(tl.time_to_x(10.0, 56, 800), 856.0)
        # Out-of-range times clamp to the drawing extent.
        self.assertEqual(tl.time_to_x(-5.0, 56, 800), 56.0)
        self.assertEqual(tl.time_to_x(50.0, 56, 800), 856.0)

    def test_x_to_time_clamps_outside_clicks(self):
        tl = make_timeline(10.0)
        self.assertEqual(tl.x_to_time(0.0, 56, 800), 0.0)
        self.assertEqual(tl.x_to_time(5000.0, 56, 800), 10.0)

    def test_scaling_on_empty_timeline_never_divides_by_zero(self):
        tl = Timeline()
        self.assertEqual(tl.time_to_x(5.0, 56, 800), 56.0)
        self.assertEqual(tl.x_to_time(400.0, 56, 800), 0.0)
        self.assertEqual(tl.time_to_x(5.0, 56, 0), 56.0)


class RulerTests(unittest.TestCase):
    def test_empty_timeline_single_zero_tick(self):
        self.assertEqual(Timeline().ruler_tick_times(), [0.0])

    def test_ticks_cover_duration_and_start_at_zero(self):
        tl = make_timeline(10.0)
        ticks = tl.ruler_tick_times()
        self.assertEqual(ticks[0], 0.0)
        self.assertLessEqual(ticks[-1], 10.0 + 1e-6)
        self.assertGreaterEqual(ticks[-1], 9.0)
        self.assertEqual(ticks, sorted(ticks))

    def test_long_video_ticks_stay_readable(self):
        tl = make_timeline(3600.0)
        ticks = tl.ruler_tick_times()
        self.assertLessEqual(len(ticks), 8)
        self.assertEqual(ticks[0], 0.0)

    def test_format_ruler_label(self):
        self.assertEqual(format_ruler_label(0.0), "0s")
        self.assertEqual(format_ruler_label(0.5), "0.5s")
        self.assertEqual(format_ruler_label(5.0), "5s")
        self.assertEqual(format_ruler_label(90.0), "1m30s")
        self.assertEqual(format_ruler_label(120.0), "2m")


class FormatTimecodeTests(unittest.TestCase):
    def test_format_timecode(self):
        self.assertEqual(format_timecode(0.0), "00:00")
        self.assertEqual(format_timecode(65.0), "01:05")
        self.assertEqual(format_timecode(3661.0), "01:01:01")
        self.assertEqual(format_timecode(-2.0), "00:00")


class ProbeWavDurationTests(unittest.TestCase):
    def test_probe_generated_wav_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = write_wav(Path(tmp) / "tone.wav", 1.5)
            self.assertAlmostEqual(probe_wav_duration(wav_path), 1.5, places=3)

    def test_probe_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                probe_wav_duration(Path(tmp) / "missing.wav")

    def test_probe_non_wav_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "not_a_wav.wav"
            bad.write_bytes(b"this is not a wav file at all")
            with self.assertRaises(Exception):
                probe_wav_duration(bad)


if __name__ == "__main__":
    unittest.main()

    def test_move_longer_than_timeline_clip_pins_to_zero(self):
        tl = make_timeline(10.0)
        tl.add_audio_clip(FAKE_WAV, 0.0, 12.0)
        tl.move_audio_clip(0, 5.0)
        self.assertEqual(tl.audio_clips[0].start, 0.0)
