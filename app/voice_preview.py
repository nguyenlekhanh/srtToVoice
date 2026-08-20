"""Piper voice preview: generation, caching and WAV playback.

Phase 2 only: synthesizes one fixed short sentence with the selected
Piper voice, caches the WAV in ``generated/previews/`` and plays it
back with the Windows-standard ``winsound`` module. No UI code here.
"""

from __future__ import annotations

import hashlib
import os
import wave
from pathlib import Path

PREVIEW_TEXT = "Hey! Look at this! This is so cool!"


class PreviewError(Exception):
    """Human-readable preview failure (safe to show in the UI)."""


def preview_cache_path(model_path: Path, previews_dir: Path) -> Path:
    """Stable cache path for a voice model.

    The file name is a hash of the absolute model path plus the file's
    size and modification time, so different voices never collide and a
    replaced model file invalidates its old cache entry.
    """
    resolved = Path(model_path).resolve()
    try:
        stat = resolved.stat()
        key = f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        key = str(resolved)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return Path(previews_dir) / f"{digest}.wav"


def generate_preview(
    model_path: Path,
    config_path: Path,
    wav_path: Path,
    text: str = PREVIEW_TEXT,
) -> None:
    """Synthesize ``text`` with Piper and write it to ``wav_path``.

    Writes to a temporary file first and renames atomically, so a
    partially generated file is never treated as a valid cache entry.
    Raises PreviewError with a concise message on any failure.
    """
    model_path = Path(model_path)
    config_path = Path(config_path)
    wav_path = Path(wav_path)

    if not model_path.is_file():
        raise PreviewError("Model file is missing.")
    if not config_path.is_file():
        raise PreviewError("Model config (.onnx.json) is missing.")

    try:
        from piper import PiperVoice
    except Exception as exc:  # pragma: no cover - environment dependent
        raise PreviewError(
            "Piper TTS is not available in this environment."
        ) from exc

    try:
        voice = PiperVoice.load(
            str(model_path), config_path=str(config_path)
        )
    except Exception as exc:
        raise PreviewError(
            "Could not load the Piper model (it may be invalid)."
        ) from exc

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = wav_path.with_name(wav_path.name + ".part")
    try:
        with wave.open(str(partial_path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        os.replace(partial_path, wav_path)
    except PreviewError:
        raise
    except Exception as exc:
        try:
            if partial_path.exists():
                partial_path.unlink()
        except OSError:
            pass
        raise PreviewError("Piper failed to generate the preview.") from exc


def is_valid_wav(wav_path: Path) -> bool:
    """True when the file exists and is a readable WAV with audio."""
    try:
        with wave.open(str(wav_path), "rb") as wav_file:
            return wav_file.getnframes() > 0
    except Exception:
        return False


def play_wav_blocking(wav_path: Path) -> None:
    """Play a WAV file, blocking until it ends or is stopped.

    Uses winsound (Windows standard library, no extra dependencies).
    """
    try:
        import winsound
    except ImportError as exc:  # pragma: no cover - non-Windows
        raise PreviewError(
            "Audio playback is not available on this platform."
        ) from exc
    winsound.PlaySound(str(wav_path), winsound.SND_FILENAME)


def stop_sound() -> None:
    """Stop any winsound playback in this process (no-op if none)."""
    try:
        import winsound
    except ImportError:  # pragma: no cover - non-Windows
        return
    try:
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass
