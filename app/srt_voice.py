"""SRT parsing and Piper voice generation (no UI code here).

Phase 3 only: parses a SubRip ``.srt`` file into plain narration text
(subtitle numbers and timestamps removed, order and punctuation kept)
and synthesizes the complete text into exactly ONE WAV file by running
the Piper CLI from the project ``.venv`` as a subprocess. Timestamps
are used only as a text source — never as timeline instructions.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import wave
from datetime import datetime
from pathlib import Path
from typing import List, Optional


class SrtError(Exception):
    """Human-readable SRT / voice generation failure (safe to show)."""


# Seconds of silence Piper inserts between sentence chunks. Because
# parse_srt_text joins cues into one line, each SRT cue becomes a
# sentence chunk and gets this pause after it (except the last one).
SENTENCE_SILENCE_SECONDS = 0.5


# "00:00:01,000 --> 00:00:03,000" (also accepts '.' milliseconds and
# trailing position metadata).
_TIMESTAMP_RE = re.compile(
    r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}"
)
_INDEX_RE = re.compile(r"^\s*\d+\s*$")
_HTML_TAG_RE = re.compile(r"<[^>]+>")       # e.g. <i>...</i>
_OVERRIDE_TAG_RE = re.compile(r"\{\\[^}]*\}")  # e.g. {\an8}


def _read_srt_content(srt_path: Path) -> str:
    """Read the file text, tolerating BOM and non-UTF-8 encodings."""
    try:
        return srt_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        pass
    except OSError as exc:
        raise SrtError("SRT file could not be read.") from exc
    try:
        return srt_path.read_text(encoding="latin-1")
    except OSError as exc:
        raise SrtError("SRT file could not be read.") from exc


def _clean_text_line(line: str) -> str:
    """Remove markup tags and collapse unnecessary whitespace."""
    line = _HTML_TAG_RE.sub("", line)
    line = _OVERRIDE_TAG_RE.sub("", line)
    return " ".join(line.split())


def _extract_block_text(block: str) -> str:
    """Extract the subtitle text of one SRT block ('' when none).

    Drops the subtitle number line and timestamp line(s); keeps the
    remaining (possibly multiline) text in order.
    """
    lines = [ln.strip() for ln in block.split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    if _INDEX_RE.match(lines[0]):
        lines = lines[1:]
    lines = [ln for ln in lines if not _TIMESTAMP_RE.match(ln)]
    lines = [_clean_text_line(ln) for ln in lines]
    lines = [ln for ln in lines if ln]
    # Multiline subtitle text becomes one flowing narration line.
    return " ".join(lines)


def parse_srt_text(srt_path: Path) -> str:
    """Extract narration text from a SubRip file.

    Subtitle numbers and timestamps are removed; subtitle order and
    punctuation are preserved; multiline subtitle text is supported.
    Subtitles are joined with single spaces into ONE flowing narration
    line, so Piper treats each cue as a separate sentence and the
    ``--sentence-silence`` gap is applied between cues. Raises SrtError
    with a concise message when the file is missing, empty or has no
    text. The original file is never modified.
    """
    srt_path = Path(srt_path)
    if not srt_path.is_file():
        raise SrtError("SRT file is missing.")

    content = _read_srt_content(srt_path)
    if not content.strip():
        raise SrtError("SRT file is empty.")

    content = content.replace("\r\n", "\n").replace("\r", "\n")
    texts: List[str] = []
    for block in re.split(r"\n\s*\n", content):
        text = _extract_block_text(block)
        if text:
            texts.append(text)

    if not texts:
        raise SrtError("SRT contains no subtitle text.")
    return " ".join(texts)


def new_voice_wav_path(generated_dir: Path) -> Path:
    """Unique timestamp-based path like ``voice_20260820_155500.wav``.

    Never overwrites an existing generated voice file.
    """
    generated_dir = Path(generated_dir)
    generated_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = generated_dir / f"voice_{stamp}.wav"
    counter = 1
    while candidate.exists():
        candidate = generated_dir / f"voice_{stamp}_{counter}.wav"
        counter += 1
    return candidate


def generate_voice_wav(
    model_path: Path,
    config_path: Path,
    text: str,
    wav_path: Path,
) -> None:
    """Synthesize the complete narration text into exactly ONE WAV.

    Runs the Piper CLI (``python -m piper``) of the project .venv as a
    subprocess. The narration text is written to a temporary UTF-8 file
    next to the output and removed afterwards. The WAV is written to a
    ``.part`` file first and renamed atomically, so a partially
    generated file is never treated as a finished asset. Raises
    SrtError with a concise message on any failure.
    """
    model_path = Path(model_path)
    config_path = Path(config_path)
    wav_path = Path(wav_path)

    if not model_path.is_file():
        raise SrtError("Piper model file is missing.")
    if not config_path.is_file():
        raise SrtError("Piper model config (.onnx.json) is missing.")
    if not text or not text.strip():
        raise SrtError("There is no narration text to generate.")

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = wav_path.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    input_path: Optional[Path] = None
    partial_path = wav_path.with_name(wav_path.name + ".part")
    try:
        fd, input_name = tempfile.mkstemp(suffix=".txt", dir=str(tmp_dir))
        input_path = Path(input_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)

        command = [
            sys.executable,
            "-m",
            "piper",
            "-m",
            str(model_path),
            "-c",
            str(config_path),
            "-i",
            str(input_path),
            "-f",
            str(partial_path),
            "--sentence-silence",
            str(SENTENCE_SILENCE_SECONDS),
        ]
        popen_kwargs = {}
        if sys.platform == "win32":
            # Do not flash a console window while Piper runs.
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **popen_kwargs,
            )
        except FileNotFoundError as exc:
            raise SrtError(
                "Piper TTS is not available in this environment."
            ) from exc

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if "No module named" in stderr:
                raise SrtError(
                    "Piper TTS is not available in this environment."
                )
            tail = stderr.splitlines()[-1].strip() if stderr else ""
            message = "Piper generation failed."
            if tail:
                message += f" ({tail[:160]})"
            raise SrtError(message)

        if not partial_path.is_file():
            raise SrtError("Piper did not produce an output WAV file.")
        try:
            with wave.open(str(partial_path), "rb") as wav_file:
                if wav_file.getnframes() <= 0:
                    raise SrtError("Generated WAV file contains no audio.")
        except wave.Error as exc:
            raise SrtError("Generated WAV file is invalid.") from exc

        os.replace(partial_path, wav_path)
    finally:
        # Always clean up the temporary input file and any partial WAV.
        if input_path is not None:
            try:
                input_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            if partial_path.exists():
                partial_path.unlink()
        except OSError:
            pass
