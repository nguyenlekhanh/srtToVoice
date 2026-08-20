# Local Video Editor (Phase 2 — Piper Voice Preview)

A local desktop application that will become a simple CapCut-like video
editor with local Piper TTS. Phase 2 adds voice preview: the selected
Piper voice synthesizes one fixed short sentence, caches the WAV in
`generated/previews/`, and plays it back locally.

## Current status

- **Media** — Upload Video / Upload SRT buttons (placeholders)
- **Voice Library** — WORKING: recursive scan of `voices/`, metadata
  display, search/filter, voice selection, refresh
- **Voice Preview** — WORKING: generates and plays a short preview of
  the selected voice with Piper; Stop supported; cached per voice
- **Video Preview** — empty preview area (placeholder)
- **Timeline** — empty timeline area (placeholder)
- **Export** — Export button (placeholder)

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
- No new third-party packages added in Phase 2

## Folder structure

```text
project/
├── .venv/          # existing virtual environment (has piper-tts)
├── app/
│   ├── __init__.py
│   ├── main.py          # UI entry point
│   ├── voice_library.py # voice scanning / metadata logic (no UI)
│   └── voice_preview.py # Piper preview generation + playback (no UI)
├── voices/         # Piper voice models (.onnx + .onnx.json), any nesting
├── generated/
│   └── previews/   # cached preview WAVs (created on first preview)
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

- Full voice generation from SRT content (Phase 3+)
- SRT parsing and upload
- Video preview and timeline editing
- FFmpeg export
