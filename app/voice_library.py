"""Voice library scanning and metadata loading (no UI code here).

Recursively scans the ``voices/`` directory for Piper voice models.
A voice is valid only when a ``model.onnx`` file has a matching
``model.onnx.json`` next to it. Metadata is read defensively: any
missing or malformed field falls back to sensible defaults, and a
broken JSON file simply causes that one voice to be skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Human-readable names for common Piper locales, used only for display
# grouping. Unknown locales fall back to the raw locale code.
LANGUAGE_NAMES = {
    "en": "English",
    "vi": "Vietnamese",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}

NO_VOICES_MESSAGE = (
    "No voices found.\n"
    "Add Piper .onnx + matching .onnx.json files to the voices folder."
)

# Piper quality levels commonly encoded in voice ids / file names,
# e.g. "en_US-arctic-medium".
QUALITY_NAMES = {"low", "medium", "high"}


@dataclass
class Voice:
    """A single discovered Piper voice."""

    model_path: Path
    json_path: Path
    name: str
    locale: str
    language: str  # display language, e.g. "English"
    gender: str
    quality: str
    relative_dir: str  # folder path relative to the voices root
    search_text: str = field(default="", repr=False)

    def display_lines(self) -> List[str]:
        """Lines shown in the voice list card."""
        lines = [self.name]
        details = " · ".join(
            part for part in (self.locale, self.gender, self.quality) if part
        )
        if details:
            lines.append(details)
        if self.relative_dir:
            lines.append(f"in {self.relative_dir}")
        return lines


def _as_str(value: object) -> str:
    """Return value as a stripped string, or '' if not usable."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _derive_locale(data: dict) -> str:
    """Extract a locale like 'en_US' from the many Piper JSON variants."""
    locale = _as_str(data.get("locale"))
    if locale:
        return locale

    language = data.get("language")
    if isinstance(language, dict):
        code = _as_str(language.get("code"))
        if code:
            return code

    voice_id = _as_str(data.get("voice_id"))
    if voice_id and "-" in voice_id:
        return voice_id.split("-", 1)[0]

    espeak = data.get("espeak")
    if isinstance(espeak, dict):
        espeak_voice = _as_str(espeak.get("voice"))
        if espeak_voice:
            # Normalize espeak ids like "en-us" to "en_US".
            parts = espeak_voice.replace("_", "-").split("-")
            if len(parts) == 2:
                return f"{parts[0].lower()}_{parts[1].upper()}"
            return espeak_voice
    return ""


def _derive_quality(data: dict, fallback_source: str) -> str:
    """Use the 'quality' field, else a low/medium/high token in the id."""
    quality = _as_str(data.get("quality"))
    if quality:
        return quality
    for token in reversed(fallback_source.split("-")):
        if token.lower() in QUALITY_NAMES:
            return token.lower()
    return ""


def _derive_name(
    data: dict, model_path: Path, locale: str, quality: str
) -> str:
    """Best display name; falls back to a prettified model file name."""
    name = _as_str(data.get("name")) or _as_str(data.get("name_display"))
    if not name:
        name = _as_str(data.get("voice_id"))
    if not name:
        name = model_path.stem  # "en_US-arctic-medium.onnx" -> without .onnx

    # Prettify "en_US-arctic-medium" -> "Arctic".
    pretty = name
    if locale and pretty.startswith(locale + "-"):
        pretty = pretty[len(locale) + 1 :]
    if quality and pretty.lower().endswith("-" + quality.lower()):
        pretty = pretty[: -(len(quality) + 1)]
    pretty = pretty.strip("-_ ")
    if pretty:
        return " ".join(part.capitalize() for part in pretty.split("-"))
    return name


def _parse_metadata(
    data: object, model_path: Path, voices_root: Path
) -> Optional[Voice]:
    """Build a Voice from parsed JSON data. Returns None if unusable."""
    if not isinstance(data, dict):
        return None

    json_path = model_path.with_name(model_path.name + ".json")
    relative_dir = str(model_path.parent.relative_to(voices_root))
    if relative_dir == ".":
        relative_dir = ""

    locale = _derive_locale(data)
    language_code = (
        locale.split("_")[0].split("-")[0].lower() if locale else ""
    )
    language = LANGUAGE_NAMES.get(language_code, language_code.upper())

    voice_id = _as_str(data.get("voice_id"))
    quality = _derive_quality(data, voice_id or model_path.stem)
    name = _derive_name(data, model_path, locale, quality)
    gender = _as_str(data.get("gender"))

    search_text = " ".join(
        (name, locale, language, gender, quality, str(model_path), relative_dir)
    ).lower()

    return Voice(
        model_path=model_path,
        json_path=json_path,
        name=name,
        locale=locale,
        language=language or "Unknown language",
        gender=gender,
        quality=quality,
        relative_dir=relative_dir,
        search_text=search_text,
    )


def scan_voices(voices_root: Path) -> List[Voice]:
    """Recursively scan ``voices_root`` for valid Piper voices.

    - Creates ``voices_root`` if it does not exist.
    - Skips ``.onnx`` files without a matching ``.onnx.json``.
    - Skips voices whose JSON is invalid or unreadable.
    - Never raises for broken individual voices.

    Returns voices sorted by (language, name).
    """
    voices_root = Path(voices_root)
    voices_root.mkdir(parents=True, exist_ok=True)

    voices: List[Voice] = []
    for onnx_path in sorted(voices_root.rglob("*.onnx")):
        if not onnx_path.is_file():
            continue
        json_path = onnx_path.with_name(onnx_path.name + ".json")
        if not json_path.is_file():
            continue  # no matching metadata -> skip silently
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, UnicodeDecodeError):
            continue  # invalid JSON -> skip this voice only
        try:
            voice = _parse_metadata(data, onnx_path, voices_root)
        except Exception:
            continue  # any unexpected metadata shape -> skip this voice
        if voice is not None:
            voices.append(voice)

    voices.sort(key=lambda v: (v.language.lower(), v.name.lower()))
    return voices


def filter_voices(voices: List[Voice], query: str) -> List[Voice]:
    """Filter voices by name, language, locale, gender, quality or path."""
    q = query.strip().lower()
    if not q:
        return list(voices)
    return [v for v in voices if q in v.search_text]
