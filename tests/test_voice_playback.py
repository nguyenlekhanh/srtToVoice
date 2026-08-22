"""Focused Bug 1B regression tests: Play/Stop Voice reliability.

Bug 1B: Stop Voice could not cancel playback because winsound was used
in blocking mode on a worker thread. The fix uses async playback
(``SND_FILENAME | SND_ASYNC``) plus Tk ``after()`` completion scheduled
from the probed WAV duration, with token checks discarding stale
completion callbacks.

No audio hardware and no real sound: ``winsound.PlaySound`` is patched
for the flag-level tests, and ``app.main.play_wav_async`` /
``app.main.stop_sound`` are patched for the App-level tests. Real Tk
``after()`` scheduling and real ``probe_wav_duration`` are exercised
against tiny silent WAV files.

Run from the project root:
    .venv\\Scripts\\python.exe -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

from app.main import App
from app.voice_preview import play_wav_async, stop_sound


def write_wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    """Write a tiny silent mono WAV of exactly ``seconds`` length."""
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


class WinsoundFlagTests(unittest.TestCase):
    """Assert the winsound flags without producing any sound."""

    def test_play_wav_async_uses_filename_and_async_flags(self):
        import winsound

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = write_wav(Path(tmp) / "clip.wav", 0.05)
            with mock.patch("winsound.PlaySound") as play_sound:
                play_wav_async(wav_path)
        play_sound.assert_called_once_with(
            str(wav_path), winsound.SND_FILENAME | winsound.SND_ASYNC
        )

    def test_stop_sound_purges_playback(self):
        import winsound

        with mock.patch("winsound.PlaySound") as play_sound:
            stop_sound()
        play_sound.assert_called_once_with(None, winsound.SND_PURGE)


class VoicePlaybackAppTestCase(unittest.TestCase):
    """Base: real App with sound output patched out."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wav_path = write_wav(Path(self._tmp.name) / "clip.wav", 0.03)
        self._closed = False
        self.play_patcher = mock.patch("app.main.play_wav_async")
        self.stop_patcher = mock.patch("app.main.stop_sound")
        self.play_mock = self.play_patcher.start()
        self.stop_mock = self.stop_patcher.start()
        self.app = App()
        self.app.update_idletasks()

    def tearDown(self):
        self.play_patcher.stop()
        self.stop_patcher.stop()
        self._tmp.cleanup()
        if not self._closed:
            # Use the app's own clean-shutdown path so pending
            # playback-completion timers are cancelled before destroy.
            try:
                self.app._on_close()
            except Exception:
                pass
            self._closed = True

    # -- helpers ----------------------------------------------------------

    def wait_until(self, predicate, timeout_s: float = 3.0) -> bool:
        """Pump the Tk event loop until ``predicate()`` or timeout."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.update()
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def asset_stop_state(self) -> str:
        return str(self.app.asset_stop_button["state"])

    def preview_stop_state(self) -> str:
        return str(self.app.stop_button["state"])

    def play_asset(self) -> None:
        self.app._on_play_asset(self.wav_path)

    def start_preview_playback(self) -> int:
        """Start preview-path playback directly (no Piper generation)."""
        self.app._preview_token += 1
        token = self.app._preview_token
        self.app._start_playback(token, self.wav_path)
        return token



class AssetPlaybackTests(VoicePlaybackAppTestCase):
    """Media Assets Play/Stop path (Phase 3 generated WAVs)."""

    def test_play_asset_starts_async_playback_and_enables_stop(self):
        self.play_asset()
        self.play_mock.assert_called_once_with(self.wav_path)
        self.assertTrue(self.app._voice_playing)
        self.assertEqual(self.asset_stop_state(), "normal")
        self.assertIn(self.wav_path.name, self.app.voice_status_label["text"])

    def test_stop_asset_stops_playback_and_resets_ui(self):
        self.play_asset()
        self.app._on_stop_asset()
        self.stop_mock.assert_called()
        self.assertFalse(self.app._voice_playing)
        self.assertEqual(self.asset_stop_state(), "disabled")
        self.assertEqual(self.app.voice_status_label["text"], "Stopped.")

    def test_natural_completion_resets_ui_state(self):
        self.play_asset()
        self.assertTrue(self.app._voice_playing)
        finished = self.wait_until(lambda: not self.app._voice_playing)
        self.assertTrue(finished, "completion callback never fired")
        self.assertEqual(self.asset_stop_state(), "disabled")
        self.assertEqual(
            self.app.voice_status_label["text"], "Playback finished."
        )

    def test_stale_completion_cannot_corrupt_newer_playback(self):
        self.play_asset()
        stale_token = self.app._voice_play_token
        self.app._on_stop_asset()  # invalidates stale_token
        self.play_asset()  # newer playback, new token
        self.assertTrue(self.app._voice_playing)
        # Stale completion from the first playback arrives late.
        self.app._on_asset_playback_done(stale_token, None)
        self.assertTrue(
            self.app._voice_playing,
            "stale completion callback corrupted the newer playback",
        )
        self.assertEqual(self.asset_stop_state(), "normal")


class PreviewPlaybackTests(VoicePlaybackAppTestCase):
    """Voice preview Play/Stop path (Phase 2)."""

    def test_preview_playback_starts_and_enables_stop(self):
        self.start_preview_playback()
        self.play_mock.assert_called_once_with(self.wav_path)
        self.assertTrue(self.app._playing)
        self.assertEqual(self.preview_stop_state(), "normal")

    def test_stop_preview_stops_playback_and_resets_ui(self):
        self.start_preview_playback()
        self.app._on_stop_preview()
        self.stop_mock.assert_called()
        self.assertFalse(self.app._playing)
        self.assertEqual(self.preview_stop_state(), "disabled")
        self.assertEqual(self.app.preview_status_label["text"], "Stopped.")

    def test_preview_natural_completion_resets_ui_state(self):
        self.start_preview_playback()
        self.assertTrue(self.app._playing)
        finished = self.wait_until(lambda: not self.app._playing)
        self.assertTrue(finished, "completion callback never fired")
        self.assertEqual(self.preview_stop_state(), "disabled")
        self.assertEqual(self.app.preview_status_label["text"], "Preview ready")

    def test_stale_preview_completion_cannot_corrupt_newer_playback(self):
        stale_token = self.start_preview_playback()
        self.app._on_stop_preview()  # invalidates stale_token
        new_token = self.start_preview_playback()  # newer playback
        self.assertNotEqual(stale_token, new_token)
        self.assertTrue(self.app._playing)
        # Stale completion from the first playback arrives late.
        self.app._on_playback_done(stale_token, None)
        self.assertTrue(
            self.app._playing,
            "stale completion callback corrupted the newer playback",
        )
        self.assertEqual(self.preview_stop_state(), "normal")


class CloseTests(VoicePlaybackAppTestCase):
    """App exit must cleanly stop any async playback."""

    def test_on_close_stops_sound(self):
        self.play_asset()
        self.assertTrue(self.app._voice_playing)
        self.app._on_close()
        self._closed = True  # _on_close destroyed the window
        self.stop_mock.assert_called()


if __name__ == "__main__":
    unittest.main()
