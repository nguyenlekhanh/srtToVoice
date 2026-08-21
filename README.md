# Local Video Editor (Phase 4 — Video Upload + Preview)

A local desktop application that will become a simple CapCut-like video
editor with local Piper TTS. Phase 4 adds local video upload and
preview: upload ONE video file and play it in the Video Preview area
with play/pause/stop, seek, time display, volume and mute. The original
video file is only read — never copied, modified or re-encoded.

## Current status

- **Media** — WORKING: Upload Video (file picker + preview), Upload SRT,
  Generate Voice, and an Assets list showing the uploaded video plus
  playable generated WAVs
- **Voice Library** — WORKING: recursive scan of `voices/`, metadata
  display, search/filter, voice selection, refresh
- **Voice Preview** — WORKING: generates and plays a short preview of
  the selected voice with Piper; Stop supported; cached per voice
- **Video Preview** — WORKING: real preview with Play/Pause, Stop,
  seek slider, current time / duration, volume slider and Mute
- **Timeline** — empty timeline area (placeholder)
- **Export** — Export button (placeholder)

## Video Upload + Preview (Phase 4)

- `Upload Video` opens a Windows file picker for video files
  (`.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`). Cancelling does nothing.
- The selected file is shown in the Media section and in the Assets
  list. Only ONE video is active at a time; uploading another video
  replaces the active preview. The original file is opened read-only
  and is NEVER copied, modified, re-encoded or overwritten.
- The Video Preview area shows a poster frame immediately after upload,
  then plays the video with synchronized audio. Frames are scaled to
  fit the preview area while preserving the aspect ratio.
- Controls: Play/Pause toggle, Stop (resets to 00:00), a seek slider,
  a `MM:SS / MM:SS` time display, a volume slider and a Mute checkbox.
  Volume and mute affect preview playback only — never the source file.
- Decoding uses PyAV (FFmpeg libraries bundled in the wheel — no system
  FFmpeg required); audio output uses sounddevice (PortAudio). All
  decoding/playback runs on background threads, so the UI stays
  responsive. Generated Phase 3 WAV assets remain visible and playable.
- Errors (unsupported format, missing file, corrupt/invalid video,
  playback failure, duration detection failure) are shown as concise
  messages in the status bar — no raw tracebacks.
- Not included (by design, later phases): timeline placement, trimming,
  audio sync/placement, drag & drop, export/rendering, save/load.

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
- Phase 4 additions (installed in the project `.venv`):
  - `av` — video/audio decoding (bundles FFmpeg libraries in the wheel,
    so no system FFmpeg install is needed and the source file is only
    ever opened read-only)
  - `sounddevice` — PortAudio playback for preview volume/mute control
  - `pillow` — decoded frames -> images for the Tkinter canvas
  - `numpy` — audio sample scaling for volume/mute

## Folder structure

```text
project/
├── .venv/          # existing virtual environment (has piper-tts)
├── app/
│   ├── __init__.py
│   ├── main.py          # UI entry point
│   ├── voice_library.py # voice scanning / metadata logic (no UI)
│   ├── voice_preview.py # Piper preview generation + playback (no UI)
│   ├── srt_voice.py     # SRT parsing + Piper WAV generation (no UI)
│   └── video_preview.py # video probing + preview playback (no UI)
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

Note: uploaded videos are NOT copied into `uploads/` — the app reads
the original file in place and never modifies it.

## Run

From the project root:

```powershell
.venv\Scripts\python.exe -m app.main
```

## Roadmap (not implemented yet)

- Timeline editing (placing video/audio on a timeline)
- Audio placement / synchronization with video
- FFmpeg export
