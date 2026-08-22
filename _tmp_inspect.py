"""Empirical Bug 1A probe: measure Piper WAV duration with and without
--sentence-silence, using realistic multi-line SRT-derived narration text.

Reproduces exactly what app/srt_voice.py does: blocks joined with blank
lines, written to a temp file, piped to `python -m piper`.
"""
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / "voices" / "en_US-amy-medium.onnx"
CONFIG = ROOT / "voices" / "en_US-amy-medium.onnx.json"

# What parse_srt_text produces for a typical multi-cue SRT: each cue's text
# on its own line, separated by blank lines.
TEXT = (
    "Hey! Look at this.\n"
    "\n"
    "This is so cool.\n"
    "\n"
    "I cannot believe it works.\n"
)


def wav_duration(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def gen(text, out, extra):
    inp = out.with_suffix(".txt")
    inp.write_text(text, encoding="utf-8")
    cmd = [
        sys.executable, "-m", "piper",
        "-m", str(MODEL), "-c", str(CONFIG),
        "-i", str(inp), "-f", str(out),
    ] + extra
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("PIPER FAILED:", proc.stderr[-500:])
        return None
    return wav_duration(out)


print("Input text (3 cues, each one sentence, joined by blank lines):")
print(repr(TEXT))
print()

d_default = gen(TEXT, ROOT / "_tmp_a_default.wav", [])
print(f"default (no --sentence-silence): {d_default:.3f}s")

d_sil = gen(TEXT, ROOT / "_tmp_a_sil.wav", ["--sentence-silence", "0.5"])
print(f"with --sentence-silence 0.5:     {d_sil:.3f}s")

if d_default and d_sil:
    print(f"delta: {d_sil - d_default:.3f}s")

# Also test: single line with 3 sentences (to see within-line sentence gaps)
TEXT_ONE = "Hey! Look at this. This is so cool. I cannot believe it works."
d_one_default = gen(TEXT_ONE, ROOT / "_tmp_a_one_default.wav", [])
d_one_sil = gen(TEXT_ONE, ROOT / "_tmp_a_one_sil.wav", ["--sentence-silence", "0.5"])
print()
print("Single line, 4 sentences:")
print(f"  default:                {d_one_default:.3f}s")
print(f"  --sentence-silence 0.5: {d_one_sil:.3f}s")
if d_one_default and d_one_sil:
    print(f"  delta: {d_one_sil - d_one_default:.3f}s")

