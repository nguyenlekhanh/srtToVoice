"""Live Bug 1A verification: REAL Piper generation through the real app
code path (parse_srt_text + generate_voice_wav). NOT mocked.

Usage:
    .venv\\Scripts\\python.exe _tmp_verify_1a.py before   (pre-fix baseline)
    .venv\\Scripts\\python.exe _tmp_verify_1a.py after    (post-fix check)

Creates a 3-cue SRT, generates the voice WAV with the project venv Piper
and prints the parsed text plus the measured WAV duration. Expected after
the fix: duration increases by ~1.0 s (2 gaps x 0.5 s for 3 cues).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.srt_voice import generate_voice_wav, parse_srt_text  # noqa: E402
from app.timeline import probe_wav_duration  # noqa: E402

MODEL = ROOT / "voices" / "en_US-amy-medium.onnx"
CONFIG = ROOT / "voices" / "en_US-amy-medium.onnx.json"

SRT = """1
00:00:01,000 --> 00:00:03,000
Hey! Look at this.

2
00:00:03,500 --> 00:00:05,500
This is so cool.

3
00:00:06,000 --> 00:00:08,500
I cannot believe it works.
"""


def main(tag: str) -> None:
    if not MODEL.is_file() or not CONFIG.is_file():
        print("FATAL: voice model missing:", MODEL)
        sys.exit(2)
    srt_path = ROOT / f"_tmp_verify_1a_{tag}.srt"
    wav_path = ROOT / f"_tmp_verify_1a_{tag}.wav"
    srt_path.write_text(SRT, encoding="utf-8")

    text = parse_srt_text(srt_path)
    print(f"[{tag}] parsed text: {text!r}")

    generate_voice_wav(MODEL, CONFIG, text, wav_path)
    duration = probe_wav_duration(wav_path)
    print(f"[{tag}] WAV: {wav_path.name}")
    print(f"[{tag}] DURATION: {duration:.3f}s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run")
