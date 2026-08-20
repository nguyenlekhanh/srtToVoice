# Local Video Editor (Phase 1 — Voice Library Scanner)

A local desktop application that will become a simple CapCut-like video
editor with local Piper TTS. Phase 1 adds a working Voice Library: the
app recursively scans the `voices/` folder for Piper voice models and
lets you select one. No audio is generated yet.

## Current status

- **Media** — Upload Video / Upload SRT buttons (placeholders)
- **Voice Library** — WORKING: recursive scan of `voices/`, metadata
  display, search/filter, voice selection, refresh
- **Video Preview** — empty preview area (placeholder)
- **Timeline** — empty timeline area (placeholder)
- **Export** — Export button (placeholder)

## Voice Library (Phase 1)

- Scans `voices/` recursively for `*.onnx` files.
- A voice is valid only when a matching `model.onnx.json` exists next
  to the `model.onnx` file.
- Missing or invalid JSON files are skipped without crashing.
- Metadata shown: name, language, locale, gender, quality, folder.
  Missing fields fall back to the model filename / folder path.
- Search filters by name, language, locale, gender, quality or path.
- `Refresh Voices` rescans and preserves the current selection when the
  same model still exists.
- The selected voice is stored in application state for Phase 2.

## Requirements

- Windows
- Python 3.12 (project `.venv` already exists)
- No third-party packages needed (UI uses tkinter from the standard
  library)

## Folder structure

```text
project/
├── .venv/          # existing virtual environment
├── app/
│   ├── __init__.py
│   ├── main.py         # UI entry point
│   └── voice_library.py # voice scanning / metadata logic (no UI)
├── voices/         # Piper voice models (.onnx + .onnx.json), any nesting
├── uploads/        # (future) uploaded videos / SRT files
├── generated/      # (future) generated voice audio
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

- Piper TTS voice generation / preview (Phase 2+)
- SRT parsing
- Video preview and timeline editing
- FFmpeg export
