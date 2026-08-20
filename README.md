# Local Video Editor (Phase 3 — SRT Voice Generation)

A local desktop application that will become a simple CapCut-like video
editor with local Piper TTS. Phase 3 adds SRT voice generation: upload
a SubRip file and generate exactly ONE complete WAV narration file with
the currently selected Piper voice.

## Current status

- **Media** — WORKING: Upload SRT (file picker), Generate Voice, and an
  Assets list with playable generated WAVs; Upload Video (placeholder)
- **Voice Library** — WORKING: recursive scan of `voices/`, metadata
  display, search/filter, voice selection, refresh
- **Voice Preview** — WORKING: generates and plays a short preview of
  the selected voice with Piper; Stop supported; cached per voice
- **Video Preview** — empty preview area (placeholder)
- **Timeline** — empty timeline area (placeholder)
- **Export** — Export button (placeholder)

## SRT Voice Generation (Phase 3)

- `Upload SRT` opens a Windows file picker for `.srt` files; the chosen
  filename is shown in the Media section. The original file is never
  modified. Cancelling does nothing.
- The SRT is parsed into plain narration text: subtitle numbers and
  timestamps are removed, subtitle order and punctuation are kept,
  multiline subtitle text is supported, and common markup tags
  (`<i>`, `{\an8}`) are stripped. Timestamps are NOT timeline
  instructions — they are only used to locate subtitle text.
- `Generate Voice` requires a selected SRT and a selected voice;
  otherwise a clear message is shown and Piper is not run.
- The complete narration text is synthesized into exactly ONE WAV in
  `generated/` (e.g. `voice_20260820_155500.wav`). Timestamp-based
  names never overwrite previous files; regenerating with another voice
  adds a new asset and keeps the old ones.
- Piper runs as a subprocess (`python -m piper`) from the project
  `.venv`, in a background worker thread; the UI stays responsive and
  shows "Generating voice..." plus the elapsed time when done.
- The temporary narration text file (under `generated/tmp/`) and any
  partial `.part` WAV are always cleaned up; the final WAV is written
  atomically.
- Generated WAVs appear in the Media Assets list with a Play button
  (local `winsound` playback) and a Stop button. Generated audio is
  NOT placed on any timeline.
- Errors (no SRT, no voice, missing/empty/malformed SRT, missing model,
  Piper unavailable, generation failure, invalid output WAV) are shown
  as concise messages — no raw tracebacks.

## Voice Preview (Phase 2)

- Fixed preview text: "Hey! Look at this! This is so cool!"
- Uses the Piper installation already in the project `.venv`
  (`piper-tts` 1.7.0). No other TTS engine is used.
- Preview WAVs are cached in `generated/previews/<hash>.wav`. The hash
  is derived from the absolute model path + file size + mtime, so
  voices never collide and replaced models invalidate their cache.
- Generation runs in a background thread; the UI stays responsive.
- Playback uses `winsound` (Windows standard library — no extra
  dependencies). Stop halts playback immediately.
- Errors (no voice selected, missing model/JSON, invalid model, Piper
  failure, playback failure) are shown as concise messages.

## Requirements

- Windows
- Python 3.12 (project `.venv` already exists, includes `piper-tts`)
- No new third-party packages added in Phase 3

## Folder structure

```text
project/
├── .venv/          # existing virtual environment (has piper-tts)
├── app/
│   ├── __init__.py
│   ├── main.py          # UI entry point
│   ├── voice_library.py # voice scanning / metadata logic (no UI)
│   ├── voice_preview.py # Piper preview generation + playback (no UI)
│   └── srt_voice.py     # SRT parsing + Piper WAV generation (no UI)
├── voices/         # Piper voice models (.onnx + .onnx.json), any nesting
├── generated/
│   ├── previews/   # cached preview WAVs (created on first preview)
│   └── tmp/        # temporary narration text files (auto-cleaned)
├── uploads/        # (future) uploaded videos / SRT files
├── projects/       # (future) saved project files
├── exports/        # (future) exported videos
├── requirements.txt
└── README.md
```

## Run

From the project root:

```powershell
.venv\Scripts\python.exe -m app.main
```

## Roadmap (not implemented yet)

- Video preview and timeline editing
- FFmpeg export
