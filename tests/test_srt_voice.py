"""Deterministic tests for app.srt_voice (Part 1 regression, Bug 1A).

No Piper subprocess and no audio hardware: SRT parsing is exercised
against real temporary ``.srt`` files, and the Piper invocation is
exercised by stubbing ``subprocess.run`` so the command line and the
temporary input file can be asserted without synthesizing any audio.
Real Piper timing verification lives outside the unit tests (recorded
in ``docs/regression_part1_voice.md``).

Run from the project root:
    .venv\\Scripts\\python.exe -m unittest discover -s tests
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from app.srt_voice import (
    SENTENCE_SILENCE_SECONDS,
    SrtError,
    generate_voice_wav,
    parse_srt_text,
)

SAMPLE_SRT = """\
1
00:00:01,000 --> 00:00:03,000
Hey! Look at this.

2
00:00:03,500 --> 00:00:05,000
This is so cool.

3
00:00:05,500 --> 00:00:07,000
I cannot believe it works.
"""

MULTILINE_CUE_SRT = """\
1
00:00:01,000 --> 00:00:03,000
First line of the cue
second line of the cue

2
00:00:03,500 --> 00:00:05,000
Next cue.
"""

EXPECTED_TEXT = (
    "Hey! Look at this. This is so cool. I cannot believe it works."
)


def write_srt(directory: Path, content: str) -> Path:
    """Write ``content`` to a temporary .srt file and return its path."""
    srt_path = directory / "sample.srt"
    srt_path.write_text(content, encoding="utf-8")
    return srt_path


def write_silent_wav(path: Path, frames: int = 160, rate: int = 16000) -> None:
    """Write a tiny valid silent mono WAV (enough to pass validation)."""
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * frames)


class ParseSrtTextTests(unittest.TestCase):
    def test_multi_cue_srt_parses_to_single_space_joined_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = write_srt(Path(tmp), SAMPLE_SRT)
            text = parse_srt_text(srt_path)
        self.assertEqual(text, EXPECTED_TEXT)
        self.assertNotIn("\n", text)

    def test_multiline_cue_text_is_joined_with_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = write_srt(Path(tmp), MULTILINE_CUE_SRT)
            text = parse_srt_text(srt_path)
        self.assertEqual(
            text, "First line of the cue second line of the cue Next cue."
        )
        self.assertNotIn("\n", text)

    def test_crlf_line_endings_are_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = write_srt(Path(tmp), SAMPLE_SRT.replace("\n", "\r\n"))
            text = parse_srt_text(srt_path)
        self.assertEqual(text, EXPECTED_TEXT)

    def test_missing_file_raises_srt_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SrtError):
                parse_srt_text(Path(tmp) / "does_not_exist.srt")


class GenerateVoiceWavCommandTests(unittest.TestCase):
    """Assert the Piper invocation without running Piper."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.model_path = root / "voice.onnx"
        self.config_path = root / "voice.onnx.json"
        self.wav_path = root / "generated" / "voice_out.wav"
        self.model_path.write_bytes(b"fake-model")
        self.config_path.write_text("{}", encoding="utf-8")
        self.captured = {}

    def tearDown(self):
        self._tmp.cleanup()

    def fake_run(self, command, **kwargs):
        """Stand-in for subprocess.run: record the command, honor the
        temp input file contract, and write a valid WAV to the -f path.
        """
        command = list(command)
        self.captured["command"] = command
        self.captured["kwargs"] = kwargs
        input_path = Path(command[command.index("-i") + 1])
        self.captured["input_path"] = input_path
        self.captured["input_text"] = input_path.read_text(encoding="utf-8")
        out_path = Path(command[command.index("-f") + 1])
        write_silent_wav(out_path)
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )

    def test_piper_command_includes_sentence_silence(self):
        with mock.patch("app.srt_voice.subprocess.run", self.fake_run):
            generate_voice_wav(
                self.model_path,
                self.config_path,
                EXPECTED_TEXT,
                self.wav_path,
            )
        command = self.captured["command"]
        self.assertIn("--sentence-silence", command)
        flag_index = command.index("--sentence-silence")
        self.assertEqual(
            command[flag_index + 1], str(SENTENCE_SILENCE_SECONDS)
        )
        self.assertEqual(SENTENCE_SILENCE_SECONDS, 0.5)

    def test_temp_input_file_contains_space_joined_text(self):
        with mock.patch("app.srt_voice.subprocess.run", self.fake_run):
            generate_voice_wav(
                self.model_path,
                self.config_path,
                EXPECTED_TEXT,
                self.wav_path,
            )
        self.assertEqual(self.captured["input_text"], EXPECTED_TEXT)
        self.assertNotIn("\n", self.captured["input_text"])

    def test_temp_input_file_is_cleaned_up_and_wav_is_final(self):
        with mock.patch("app.srt_voice.subprocess.run", self.fake_run):
            generate_voice_wav(
                self.model_path,
                self.config_path,
                EXPECTED_TEXT,
                self.wav_path,
            )
        self.assertFalse(self.captured["input_path"].exists())
        self.assertTrue(self.wav_path.is_file())
        self.assertFalse(
            self.wav_path.with_name(self.wav_path.name + ".part").exists()
        )


if __name__ == "__main__":
    unittest.main()
