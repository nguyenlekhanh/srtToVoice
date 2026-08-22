"""Local CapCut-like editor skeleton: Voice Library, Voice Preview,
SRT voice generation and video preview.

Tkinter UI with five sections: Media, Voice, Video Preview, Timeline,
Export.

- Phase 1: the Voice section scans the ``voices/`` directory
  recursively for Piper ``.onnx`` + ``.onnx.json`` pairs, shows their
  metadata, and lets the user select a voice.
- Phase 2: the selected voice can be previewed (cached WAV generation
  in ``generated/previews/`` plus local playback).
- Phase 3: ``Upload SRT`` picks a SubRip file, ``Generate Voice``
  synthesizes the complete subtitle text into exactly ONE WAV in
  ``generated/`` using the selected Piper voice. The SRT timestamps
  are only a text source — no timeline placement happens.
- Phase 4: ``Upload Video`` picks ONE local video file and shows it in
  the Video Preview area with play/pause/stop, seek, time display,
  volume and mute. The original file is only read — never copied,
  modified or re-encoded. No timeline placement, trimming or export.
- Phase 5: a real (non-destructive, metadata-only) Timeline with a
  time ruler, a VIDEO track showing the active video clip, an empty
  AUDIO track and a playhead that follows the VideoPlayer. Clicking
  the timeline seeks the video.
- Phase 6: generated WAV assets can be dragged from the Media/Assets
  panel onto the AUDIO track to create AudioClips (metadata-only
  references, WAV duration read from the file). AudioClips can then be
  moved horizontally by dragging, and selected by clicking. No trimming,
  splitting, snapping, mixing or playback of timeline audio yet.
- Phase 7: timeline editing workflow. Audio clips are kept within the
  timeline bounds (drag/drop clamped by the model), WAVs longer than
  the timeline are rejected on drop with a status message, clicking
  anywhere that is not a clip deselects the selected clip, overlapping
  clips resolve to the topmost one, and audio clips survive video
  changes (re-clamped into the new duration). No trimming, snapping,
  undo/redo or mixing yet.
- Phase 8: audio clip trimming. Dragging the left or right edge of an
  already-selected audio clip trims (shortens) it live; trimming never
  EXTENDS a clip, never goes below MIN_AUDIO_CLIP_DURATION and never
  crosses the timeline bounds. A resize cursor appears while hovering
  over the edges of the selected clip, and the selected clip shows
  edge handles. The WAV files are never touched. No video trimming,
  splitting, snapping, undo/redo or mixing yet.

Export remains a placeholder.

Run with the project's .venv:
    .venv\\Scripts\\python.exe -m app.main
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, List, Optional

from PIL import Image, ImageTk

from app.srt_voice import (
    SrtError,
    generate_voice_wav,
    new_voice_wav_path,
    parse_srt_text,
)
from app.timeline import Timeline, format_ruler_label, probe_wav_duration
from app.video_preview import (
    SUPPORTED_VIDEO_EXTENSIONS,
    VideoError,
    VideoPlayer,
    format_timecode,
    probe_video,
)
from app.voice_library import (
    NO_VOICES_MESSAGE,
    Voice,
    filter_voices,
    scan_voices,
)
from app.voice_preview import (
    PREVIEW_TEXT,
    PreviewError,
    generate_preview,
    is_valid_wav,
    play_wav_async,
    preview_cache_path,
    stop_sound,
)

APP_TITLE = "Local Video Editor — Phase 8 (Audio Clip Trimming)"
COMING_SOON = "Coming in next phases"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VOICES_ROOT = PROJECT_ROOT / "voices"
PREVIEWS_DIR = PROJECT_ROOT / "generated" / "previews"
GENERATED_DIR = PROJECT_ROOT / "generated"

BG = "#f2f2f2"
PANEL_BG = "#ffffff"
PREVIEW_BG = "#1e1e1e"

# Phase 5 timeline colors.
TIMELINE_BG = "#fafafa"
RULER_BG = "#ececec"
RULER_LINE = "#b5b5b5"
RULER_TEXT = "#555555"
TRACK_LABEL = "#666666"
VIDEO_CLIP_FILL = "#4a90d9"
VIDEO_CLIP_EDGE = "#2f6cb0"
VIDEO_CLIP_TEXT = "#ffffff"
AUDIO_TRACK_FILL = "#f0f0f0"
AUDIO_TRACK_EDGE = "#d5d5d5"
AUDIO_PLACEHOLDER_TEXT = "#a8a8a8"
PLAYHEAD_COLOR = "#e03131"

# Phase 6 audio clip colors (distinct from video clip).
AUDIO_CLIP_FILL = "#6fbe6f"
AUDIO_CLIP_EDGE = "#3d8f3d"
AUDIO_CLIP_TEXT = "#123012"
AUDIO_CLIP_SELECTED_FILL = "#a6e3a6"
AUDIO_CLIP_SELECTED_EDGE = "#1f7a1f"

# Phase 8 trim UI constants.
#: Pointer distance (px) from a selected clip's edge that grabs it for
#: trimming. Small enough to keep clip-body presses as Phase 7 moves.
EDGE_GRAB_PX = 6
#: Drawn width (px) of the trim handles on the selected clip's edges.
CLIP_HANDLE_W = 4.0


class App(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1150x720")
        self.minsize(950, 580)
        self.configure(bg=BG)

        # Phase 1 application state: discovered voices + selection.
        self.voices: List[Voice] = []
        self.selected_voice: Optional[Voice] = None
        self.search_var = tk.StringVar()

        # Phase 2 preview state (generation + playback).
        self._preview_token = 0  # invalidates stale async callbacks
        self._generating = False
        self._playing = False
        self._preview_after_id = None  # pending playback-completion timer
        # Thread-safe handoff: worker threads enqueue callbacks here and
        # the Tkinter main thread executes them via _poll_ui_queue().
        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

        # Phase 3 state: selected SRT + generated voice assets.
        self.srt_path: Optional[Path] = None
        self.generated_wavs: List[Path] = []
        self._voice_generating = False
        self._voice_playing = False
        self._voice_play_token = 0  # invalidates stale playback callbacks
        self._voice_after_id = None  # pending playback-completion timer

        # Phase 4 state: uploaded video + preview player.
        self.video_path: Optional[Path] = None
        self.video_player: Optional[VideoPlayer] = None
        self._video_photo: Optional[ImageTk.PhotoImage] = None  # keep ref
        self._video_seek_pending = False  # suppress seek-slider feedback
        self._seek_after_id = None  # pending seek-settle timer (RC6)
        self._video_tick_token = 0  # invalidates stale tick callbacks

        # Phase 5 state: non-destructive timeline model (metadata only).
        # The VideoPlayer is the single source of truth for playback
        # time; the timeline playhead only mirrors it.
        self.timeline = Timeline()

        # Phase 6 drag/drop state. ``self._drag`` is None while idle, or a
        # small dict describing an in-progress drag:
        #   {"kind": "media", "path": Path}  (asset dragged from Media panel)
        #   {"kind": "clip", "index": int, "offset": float}  (clip being moved)
        #   {"kind": "trim", "index": int, "edge": "left"|"right"}  (Phase 8)
        self._drag = None

        self._build_layout()

        self._refresh_voices(initial=True)
        self._poll_after_id = self.after(50, self._poll_ui_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------- thread-safe UI updates

    def _run_on_ui_thread(self, callback: Callable[[], None]) -> None:
        """Schedule ``callback`` to run on the Tkinter main thread."""
        self._ui_queue.put(callback)

    def _poll_ui_queue(self) -> None:
        """Execute queued worker callbacks on the main thread."""
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception:
                    # Never let a bad callback kill the polling loop.
                    pass
        except queue.Empty:
            pass
        self._poll_after_id = self.after(50, self._poll_ui_queue)

    # ------------------------------------------------------------------ UI

    def _build_layout(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass  # fall back to the default theme on non-Windows systems

        root = ttk.Frame(self, padding=8)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=3)  # preview area
        root.rowconfigure(1, weight=1)  # timeline area

        self._build_sidebar(root)
        self._build_preview(root)
        self._build_timeline(root)
        self._build_status_bar()

        # App-wide drag tracking. These receive every button-motion /
        # button-release regardless of which widget is under the pointer,
        # which is what allows dragging an asset from the Media panel onto
        # the timeline canvas. Each handler no-ops unless ``self._drag``
        # is active.
        self.bind_all("<B1-Motion>", self._on_global_drag_motion)
        self.bind_all("<ButtonRelease-1>", self._on_global_drag_release)

    def _section(self, parent: ttk.Widget, title: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        return frame

    def _coming_soon_button(
        self, parent: ttk.Widget, label: str
    ) -> ttk.Button:
        """Button that only reports the feature is not implemented yet."""
        return ttk.Button(
            parent,
            text=label,
            command=lambda l=label: self._set_status(f"'{l}' — {COMING_SOON}."),
        )

    def _build_sidebar(self, parent: ttk.Widget) -> None:
        sidebar = ttk.Frame(parent)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 8))

        # 1. Media (Phase 3: SRT upload + voice generation,
        #           Phase 4: video upload)
        media = self._section(sidebar, "Media")
        ttk.Button(
            media, text="Upload Video", command=self._on_upload_video
        ).pack(fill=tk.X, pady=2)
        self.video_label = ttk.Label(
            media,
            text="Video: (none)",
            foreground="#444444",
            wraplength=260,
            justify="left",
        )
        self.video_label.pack(anchor="w", padx=2)
        ttk.Button(media, text="Upload SRT", command=self._on_upload_srt).pack(
            fill=tk.X, pady=2
        )
        self.srt_label = ttk.Label(
            media,
            text="SRT: (none)",
            foreground="#444444",
            wraplength=260,
            justify="left",
        )
        self.srt_label.pack(anchor="w", padx=2)
        self.generate_voice_button = ttk.Button(
            media, text="Generate Voice", command=self._on_generate_voice
        )
        self.generate_voice_button.pack(fill=tk.X, pady=(6, 2))
        self.voice_status_label = ttk.Label(
            media, text="", foreground="#444444", wraplength=260, justify="left"
        )
        self.voice_status_label.pack(anchor="w", padx=2)

        # Generated voice assets (playback only — never placed on a timeline)
        self.assets_frame = ttk.LabelFrame(media, text="Assets", padding=6)
        self.assets_frame.pack(fill=tk.X, pady=(6, 0))
        self._rebuild_assets_list()

        # 2. Voice Library (Phase 1)
        self._build_voice_section(sidebar)

        # 5. Export (unchanged placeholder)
        export = self._section(sidebar, "Export")
        self._coming_soon_button(export, "Export").pack(fill=tk.X, pady=2)

        note = ttk.Label(
            sidebar,
            text="Export is a placeholder for now.",
            foreground="#666666",
        )
        note.pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------- Voice section

    def _build_voice_section(self, parent: ttk.Widget) -> None:
        voice = ttk.LabelFrame(parent, text="Voice Library", padding=8)
        voice.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        voice.columnconfigure(0, weight=1)
        voice.rowconfigure(5, weight=1)

        # Search / filter
        search_row = ttk.Frame(voice)
        search_row.grid(row=0, column=0, sticky="ew")
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="Search:").grid(row=0, column=0, padx=(0, 4))
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky="ew")
        self.search_var.trace_add("write", lambda *_: self._rebuild_voice_list())

        # Refresh
        ttk.Button(voice, text="Refresh Voices", command=self._refresh_voices).grid(
            row=1, column=0, sticky="ew", pady=(6, 0)
        )

        # Selected voice display
        self.selected_label = ttk.Label(
            voice,
            text="Selected voice: (none)",
            foreground="#0a5c2e",
            wraplength=260,
            justify="left",
        )
        self.selected_label.grid(row=2, column=0, sticky="ew", pady=(8, 4))

        # Phase 2: preview controls
        preview_row = ttk.Frame(voice)
        preview_row.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        preview_row.columnconfigure(0, weight=1)
        preview_row.columnconfigure(1, weight=1)
        self.preview_button = ttk.Button(
            preview_row, text="Preview Voice", command=self._on_preview_voice
        )
        self.preview_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_button = ttk.Button(
            preview_row, text="Stop", command=self._on_stop_preview, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, sticky="ew")

        self.preview_status_label = ttk.Label(
            voice, text="", foreground="#444444", wraplength=260, justify="left"
        )
        self.preview_status_label.grid(row=4, column=0, sticky="ew", pady=(4, 0))

        # Scrollable voice list
        list_frame = ttk.Frame(voice)
        list_frame.grid(row=5, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.voice_canvas = tk.Canvas(
            list_frame, bg=PANEL_BG, highlightthickness=1,
            highlightbackground="#cccccc", width=270,
        )
        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.voice_canvas.yview
        )
        self.voice_canvas.configure(yscrollcommand=scrollbar.set)
        self.voice_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.voice_list_inner = ttk.Frame(self.voice_canvas)
        self._list_window = self.voice_canvas.create_window(
            (0, 0), window=self.voice_list_inner, anchor="nw"
        )
        self.voice_list_inner.bind(
            "<Configure>",
            lambda _e: self.voice_canvas.configure(
                scrollregion=self.voice_canvas.bbox("all")
            ),
        )
        self.voice_canvas.bind(
            "<Configure>",
            lambda e: self.voice_canvas.itemconfigure(self._list_window, width=e.width),
        )
        # Mouse wheel scrolling
        self.voice_canvas.bind("<Enter>", self._bind_mousewheel)
        self.voice_canvas.bind("<Leave>", self._unbind_mousewheel)

    def _bind_mousewheel(self, _event: tk.Event) -> None:
        self.voice_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event: tk.Event) -> None:
        self.voice_canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.voice_canvas.yview_scroll(int(-event.delta / 120), "units")

    # --------------------------------------------------- Voice list logic

    def _refresh_voices(self, initial: bool = False) -> None:
        """Rescan voices/, rebuild the list, preserve selection if possible."""
        previous_path = (
            self.selected_voice.model_path if self.selected_voice else None
        )

        self.voices = scan_voices(VOICES_ROOT)

        # Preserve selection when the same model still exists.
        self.selected_voice = None
        if previous_path is not None:
            for voice in self.voices:
                if voice.model_path == previous_path:
                    self.selected_voice = voice
                    break
        self._update_selected_label()
        self._rebuild_voice_list()

        if self.voices:
            message = f"Voice library loaded: {len(self.voices)} voice(s) found."
        else:
            message = NO_VOICES_MESSAGE.replace("\n", " ")
        if not initial:
            message = message.replace("loaded", "refreshed", 1)
        self._set_status(message)

    def _rebuild_voice_list(self) -> None:
        """Rebuild the visible voice cards from current state + filter."""
        for child in self.voice_list_inner.winfo_children():
            child.destroy()

        visible = filter_voices(self.voices, self.search_var.get())

        if not self.voices:
            ttk.Label(
                self.voice_list_inner,
                text=NO_VOICES_MESSAGE,
                wraplength=250,
                justify="left",
                foreground="#666666",
            ).pack(anchor="w", padx=6, pady=6)
            return

        if not visible:
            ttk.Label(
                self.voice_list_inner,
                text="No voices match the search.",
                foreground="#666666",
            ).pack(anchor="w", padx=6, pady=6)
            return

        current_language = None
        for voice in visible:
            if voice.language != current_language:
                current_language = voice.language
                header = ttk.Label(
                    self.voice_list_inner,
                    text=current_language,
                    font=("Segoe UI", 9, "bold"),
                )
                header.pack(anchor="w", padx=6, pady=(8, 0))
                ttk.Separator(self.voice_list_inner, orient="horizontal").pack(
                    fill=tk.X, padx=6, pady=(2, 4)
                )
            self._add_voice_card(voice)

    def _add_voice_card(self, voice: Voice) -> None:
        is_selected = (
            self.selected_voice is not None
            and self.selected_voice.model_path == voice.model_path
        )
        card = ttk.LabelFrame(
            self.voice_list_inner,
            text="Selected" if is_selected else "",
            padding=6,
        )
        card.pack(fill=tk.X, padx=6, pady=3)

        for i, line in enumerate(voice.display_lines()):
            label = ttk.Label(
                card,
                text=line,
                font=("Segoe UI", 9, "bold") if i == 0 else ("Segoe UI", 8),
                foreground="#222222" if i == 0 else "#555555",
                wraplength=230,
            )
            label.pack(anchor="w")

        button = ttk.Button(
            card,
            text="Selected" if is_selected else "Select",
            command=lambda v=voice: self._select_voice(v),
        )
        button.pack(anchor="e", pady=(4, 0))
        if is_selected:
            button.state(["disabled"])

    def _select_voice(self, voice: Voice) -> None:
        """Store the selected voice in application state (no audio yet)."""
        self._cancel_preview_if_active()
        self.selected_voice = voice
        self._update_selected_label()
        self._rebuild_voice_list()
        self._set_status(
            f"Selected voice: {voice.name} ({voice.locale or 'unknown locale'}) "
            f"— model: {voice.model_path}"
        )

    def _update_selected_label(self) -> None:
        if self.selected_voice is None:
            self.selected_label.configure(text="Selected voice: (none)")
            return
        v = self.selected_voice
        self.selected_label.configure(
            text=(
                f"Selected voice: {v.name}\n"
                f"Locale: {v.locale or 'unknown'}\n"
                f"Model: {v.model_path}"
            )
        )

    # ------------------------------------------------- Phase 2: preview

    def _set_preview_status(self, message: str) -> None:
        self.preview_status_label.configure(text=message)

    def _set_preview_busy(self, busy: bool) -> None:
        """Toggle button states for generating/playing vs idle."""
        self.preview_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _on_preview_voice(self) -> None:
        """Generate (or reuse) a cached preview and play it."""
        if self._generating or self._playing:
            return
        if self.selected_voice is None:
            self._set_preview_status("No voice selected. Select a voice first.")
            self._set_status("No voice selected. Select a voice first.")
            return

        voice = self.selected_voice
        wav_path = preview_cache_path(voice.model_path, PREVIEWS_DIR)
        self._preview_token += 1
        token = self._preview_token

        if wav_path.is_file() and is_valid_wav(wav_path):
            # Cached preview exists: play it without invoking Piper again.
            self._set_preview_status("Preview ready (cached)")
            self._start_playback(token, wav_path, playing_status="Playing... (cached)")
            return

        # Generate in a background thread so the UI stays responsive.
        self._generating = True
        self._set_preview_busy(True)
        self._set_preview_status("Generating preview...")
        self._set_status(f"Generating preview for '{voice.name}'...")
        worker = threading.Thread(
            target=self._generate_worker,
            args=(token, voice, wav_path),
            daemon=True,
        )
        worker.start()

    def _generate_worker(
        self, token: int, voice: Voice, wav_path: Path
    ) -> None:
        """Background thread: run Piper, then report back to the UI."""
        error: Optional[str] = None
        try:
            generate_preview(voice.model_path, voice.json_path, wav_path)
        except PreviewError as exc:
            error = str(exc)
        except Exception:
            error = "Preview generation failed."
        self._run_on_ui_thread(
            lambda: self._on_generation_done(token, wav_path, error)
        )

    def _on_generation_done(
        self, token: int, wav_path: Path, error: Optional[str]
    ) -> None:
        self._generating = False
        if token != self._preview_token:
            return  # superseded by a newer preview request or Stop
        if error is not None:
            self._set_preview_busy(False)
            self._set_preview_status(error)
            self._set_status(error)
            return
        self._set_preview_status("Preview ready")
        self._start_playback(token, wav_path)

    def _start_playback(
        self, token: int, wav_path: Path, playing_status: str = "Playing..."
    ) -> None:
        """Start async winsound playback and schedule its completion.

        Bug 1B fix: playback is non-blocking (``SND_FILENAME |
        SND_ASYNC``), so no worker thread is needed and Stop Voice can
        purge it from the UI thread. Completion is scheduled with Tk
        ``after()`` using the probed WAV duration; the token check in
        ``_on_playback_done`` discards the callback if Stop happened.
        """
        self._playing = True
        self._set_preview_busy(True)
        self._set_preview_status(playing_status)
        try:
            duration = probe_wav_duration(wav_path)
            play_wav_async(wav_path)
        except PreviewError as exc:
            self._on_playback_done(token, str(exc))
            return
        except Exception:
            self._on_playback_done(token, "Playback failed.")
            return
        delay_ms = max(0, int(duration * 1000))
        self._cancel_after("_preview_after_id")
        self._preview_after_id = self.after(
            delay_ms, lambda: self._on_playback_done(token, None)
        )

    def _cancel_after(self, attr: str) -> None:
        """Cancel a pending ``after`` timer stored on ``attr`` (if any)."""
        after_id = getattr(self, attr, None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
            setattr(self, attr, None)

    def _on_playback_done(self, token: int, error: Optional[str]) -> None:
        if token != self._preview_token:
            return  # stopped or superseded — never touch current state
        self._preview_after_id = None
        self._playing = False
        self._set_preview_busy(False)
        if error is not None:
            self._set_preview_status(error)
            self._set_status(error)
        else:
            self._set_preview_status("Preview ready")
            self._set_status("Preview finished.")

    def _on_stop_preview(self) -> None:
        """Stop playback and cancel any in-flight preview request."""
        self._preview_token += 1  # invalidate pending worker callbacks
        self._generating = False
        self._playing = False
        self._cancel_after("_preview_after_id")
        stop_sound()
        self._set_preview_busy(False)
        self._set_preview_status("Stopped.")
        self._set_status("Preview stopped.")

    def _cancel_preview_if_active(self) -> None:
        """Silently stop preview when the selected voice changes."""
        if self._generating or self._playing:
            self._preview_token += 1
            self._generating = False
            self._playing = False
            self._cancel_after("_preview_after_id")
            stop_sound()
            self._set_preview_busy(False)
            self._set_preview_status("")

    # ------------------------------------- Phase 3: SRT + voice generation

    def _set_voice_status(self, message: str) -> None:
        self.voice_status_label.configure(text=message)

    def _update_voice_controls(self) -> None:
        """Sync button states with generating / playing flags."""
        self.generate_voice_button.configure(
            state="disabled" if self._voice_generating else "normal"
        )
        if getattr(self, "asset_stop_button", None) is not None:
            self.asset_stop_button.configure(
                state="normal" if self._voice_playing else "disabled"
            )

    def _on_upload_srt(self) -> None:
        """Pick an .srt file and store its path in application state."""
        file_path = filedialog.askopenfilename(
            title="Select an SRT file",
            filetypes=[("SubRip subtitles", "*.srt"), ("All files", "*.*")],
        )
        if not file_path:
            return  # user cancelled -> do nothing
        self.srt_path = Path(file_path)
        self.srt_label.configure(text=f"SRT: {self.srt_path.name}")
        self._set_status(f"SRT selected: {self.srt_path}")

    def _on_generate_voice(self) -> None:
        """Generate ONE WAV from the complete SRT narration text."""
        if self._voice_generating:
            return
        if self.srt_path is None:
            self._set_voice_status("No SRT selected. Upload an SRT file first.")
            self._set_status("No SRT selected. Upload an SRT file first.")
            return
        if self.selected_voice is None:
            self._set_voice_status("No voice selected. Select a voice first.")
            self._set_status("No voice selected. Select a voice first.")
            return

        try:
            text = parse_srt_text(self.srt_path)
        except SrtError as exc:
            self._set_voice_status(str(exc))
            self._set_status(str(exc))
            return

        voice = self.selected_voice
        wav_path = new_voice_wav_path(GENERATED_DIR)
        self._voice_generating = True
        self._update_voice_controls()
        self._set_voice_status("Generating voice...")
        self._set_status("Generating voice...")
        started_at = time.monotonic()
        threading.Thread(
            target=self._generate_voice_worker,
            args=(voice.model_path, voice.json_path, text, wav_path, started_at),
            daemon=True,
        ).start()

    def _generate_voice_worker(
        self,
        model_path: Path,
        config_path: Path,
        text: str,
        wav_path: Path,
        started_at: float,
    ) -> None:
        """Background Piper generation (never touches Tkinter directly)."""
        error: Optional[str] = None
        try:
            generate_voice_wav(model_path, config_path, text, wav_path)
        except SrtError as exc:
            error = str(exc)
        except Exception:
            error = "Voice generation failed unexpectedly."
        elapsed = time.monotonic() - started_at
        self._run_on_ui_thread(
            lambda: self._on_voice_generated(wav_path, error, elapsed)
        )

    def _on_voice_generated(
        self, wav_path: Path, error: Optional[str], elapsed: float
    ) -> None:
        self._voice_generating = False
        self._update_voice_controls()
        if error is not None:
            self._set_voice_status(error)
            self._set_status(error)
            return
        if not is_valid_wav(wav_path):
            message = "Generated WAV file is missing or invalid."
            self._set_voice_status(message)
            self._set_status(message)
            return
        # Keep previous generated assets; add the new one as well.
        self.generated_wavs.append(wav_path)
        self._rebuild_assets_list()
        message = f"Voice generated successfully. ({elapsed:.1f}s)"
        self._set_voice_status(message)
        self._set_status(f"{message} -> {wav_path.name}")

    def _rebuild_assets_list(self) -> None:
        """Show the uploaded video + generated WAVs as media assets."""
        for child in self.assets_frame.winfo_children():
            child.destroy()

        ttk.Label(
            self.assets_frame,
            text="Video:",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")

        if self.video_path is None:
            ttk.Label(
                self.assets_frame,
                text="No video uploaded yet.",
                foreground="#888888",
            ).pack(anchor="w", pady=(0, 2))
        else:
            row = ttk.Frame(self.assets_frame)
            row.pack(fill=tk.X, pady=1)
            row.columnconfigure(0, weight=1)
            ttk.Label(
                row,
                text=f"\U0001F3AC {self.video_path.name}",
                wraplength=230,
            ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            self.assets_frame,
            text="Audio:",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(4, 0))

        if not self.generated_wavs:
            ttk.Label(
                self.assets_frame,
                text="No generated audio yet.",
                foreground="#888888",
            ).pack(anchor="w", pady=(0, 2))
        else:
            for wav_path in self.generated_wavs:
                row = ttk.Frame(self.assets_frame)
                row.pack(fill=tk.X, pady=1)
                row.columnconfigure(0, weight=1)
                asset_label = ttk.Label(
                    row,
                    text=f"\U0001F50A {wav_path.name}",
                    wraplength=230,
                    cursor="fleur",
                )
                asset_label.grid(row=0, column=0, sticky="w")
                asset_label.bind(
                    "<ButtonPress-1>",
                    lambda _e, p=wav_path: self._on_asset_drag_start(p),
                )
                row.bind(
                    "<ButtonPress-1>",
                    lambda _e, p=wav_path: self._on_asset_drag_start(p),
                )
                ttk.Button(
                    row,
                    text="\u25B6 Play",
                    width=8,
                    command=lambda p=wav_path: self._on_play_asset(p),
                ).grid(row=0, column=1, sticky="e")

        self.asset_stop_button = ttk.Button(
            self.assets_frame,
            text="Stop",
            command=self._on_stop_asset,
            state="normal" if self._voice_playing else "disabled",
        )
        self.asset_stop_button.pack(fill=tk.X, pady=(4, 0))

    def _on_play_asset(self, wav_path: Path) -> None:
        """Play a generated WAV locally (async winsound, Bug 1B fix)."""
        if self._voice_playing:
            return
        if not is_valid_wav(wav_path):
            self._set_voice_status("This WAV file is missing or invalid.")
            return
        self._cancel_preview_if_active()  # winsound plays one sound at a time
        self._voice_play_token += 1
        token = self._voice_play_token
        self._voice_playing = True
        self._update_voice_controls()
        self._set_voice_status(f"Playing {wav_path.name}...")
        try:
            duration = probe_wav_duration(wav_path)
            play_wav_async(wav_path)
        except PreviewError as exc:
            self._on_asset_playback_done(token, str(exc))
            return
        except Exception:
            self._on_asset_playback_done(token, "Playback failed.")
            return
        delay_ms = max(0, int(duration * 1000))
        self._cancel_after("_voice_after_id")
        self._voice_after_id = self.after(
            delay_ms, lambda: self._on_asset_playback_done(token, None)
        )

    def _on_asset_playback_done(self, token: int, error: Optional[str]) -> None:
        if token != self._voice_play_token:
            return  # stopped or superseded — never touch current state
        self._voice_after_id = None
        self._voice_playing = False
        self._update_voice_controls()
        if error is not None:
            self._set_voice_status(error)
        else:
            self._set_voice_status("Playback finished.")

    def _on_stop_asset(self) -> None:
        """Stop generated-audio playback and cancel pending callbacks."""
        self._voice_play_token += 1
        self._voice_playing = False
        self._cancel_after("_voice_after_id")
        stop_sound()
        self._update_voice_controls()
        self._set_voice_status("Stopped.")
        self._set_status("Audio playback stopped.")

    # ------------------------------------- Phase 4: video upload + preview

    def _on_upload_video(self) -> None:
        """Pick ONE local video file and load it into the preview."""
        filetypes = [
            ("Video files", " ".join(f"*{ext}" for ext in sorted(
                SUPPORTED_VIDEO_EXTENSIONS
            ))),
            ("All files", "*.*"),
        ]
        file_path = filedialog.askopenfilename(
            title="Select a video file",
            filetypes=filetypes,
        )
        if not file_path:
            return  # user cancelled -> do nothing
        self._load_video(Path(file_path))

    def _load_video(self, path: Path) -> None:
        """Validate, probe and activate a video for preview."""
        # Stop any current playback and release the previous player.
        self._teardown_video_player()

        try:
            info = probe_video(path)
        except VideoError as exc:
            self._set_status(str(exc))
            self.video_label.configure(text="Video: (none)")
            self.timeline.clear_video()
            self._redraw_timeline()
            self._rebuild_assets_list()
            return

        # Create the player wired to this UI.
        try:
            self.video_player = VideoPlayer(
                path,
                on_frame=self._on_video_frame,
                on_tick=self._on_video_tick,
                on_finished=self._on_video_finished,
                on_error=self._on_video_error,
            )
        except VideoError as exc:
            self._set_status(str(exc))
            self.video_label.configure(text="Video: (none)")
            return

        self.video_path = path
        self._video_tick_token += 1

        # Phase 5: sync the timeline with the uploaded video. The
        # timeline only stores metadata (path + duration); the source
        # file itself is never copied or modified.
        self.timeline.set_video(path, info.duration)
        self._redraw_timeline()

        # Update UI state.
        self.video_label.configure(text=f"Video: {path.name}")
        self._rebuild_assets_list()
        self._set_video_controls_enabled(True)
        self._update_time_label(0.0, info.duration)
        self.seek_var.set(0.0)
        self.seek_slider.configure(to=info.duration)
        self._set_status(
            f"Video loaded: {path.name} "
            f"({info.width}x{info.height}, {format_timecode(info.duration)})"
        )

        # Show the first frame as a poster.
        self.video_player.show_first_frame()

    def _teardown_video_player(self) -> None:
        """Stop and discard the current video player, if any."""
        if self.video_player is not None:
            self.video_player.close()
            self.video_player = None
        self._video_tick_token += 1
        # Bug 2 (RC6): discard any pending seek-settle timer so it does
        # not fire against a different (or no) player.
        self._cancel_after("_seek_after_id")
        self._video_seek_pending = False

    def _set_video_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.play_pause_button.configure(state=state)
        self.stop_video_button.configure(state=state)
        self.seek_slider.configure(state=state)
        self.mute_button.configure(state=state)
        self.volume_slider.configure(state=state)
        if not enabled:
            self.play_pause_button.configure(text="\u25B6 Play")

    def _on_video_frame(self, image) -> None:
        """Receive a decoded frame (PIL image) from the player thread."""
        self._run_on_ui_thread(lambda: self._draw_video_frame(image))

    def _draw_video_frame(self, image) -> None:
        canvas = self.preview_canvas
        cw = canvas.winfo_width()
        ch = canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return
        # Fit the frame into the canvas while preserving aspect ratio.
        iw, ih = image.size
        scale = min(cw / iw, ch / ih)
        new_w = max(1, int(iw * scale))
        new_h = max(1, int(ih * scale))
        if (new_w, new_h) != (iw, ih):
            image = image.resize((new_w, new_h), Image.LANCZOS)
        self._video_photo = ImageTk.PhotoImage(image)
        canvas.delete("placeholder")
        canvas.delete("frame")
        canvas.create_image(cw / 2, ch / 2, image=self._video_photo, tags="frame")

    def _on_preview_resize(self, event: tk.Event) -> None:
        canvas = self.preview_canvas
        if self.video_player is None:
            canvas.coords("placeholder", event.width / 2, event.height / 2)
            return
        # Re-render a frame so the image fits the new canvas size.
        if not self.video_player.is_active:
            self.video_player.show_first_frame()

    def _on_video_tick(self, position: float, duration: float) -> None:
        token = self._video_tick_token
        self._run_on_ui_thread(
            lambda: self._apply_video_tick(token, position, duration)
        )

    def _apply_video_tick(
        self, token: int, position: float, duration: float
    ) -> None:
        if token != self._video_tick_token:
            return  # superseded by a newer video
        # Bug 2 (RC6): while a seek is settling, ignore ticks entirely —
        # pre-seek ticks would otherwise snap the time label and the
        # timeline playhead back to the old position (visible jump).
        if self._video_seek_pending:
            return
        self._update_time_label(position, duration)
        if duration > 0:
            self.seek_var.set(position)
        # Phase 5: VideoPlayer is the single source of truth for the
        # current time; the timeline playhead only mirrors it.
        self.timeline.set_playhead(position)
        self._update_playhead()

    def _update_time_label(self, position: float, duration: float) -> None:
        self.time_label.configure(
            text=f"{format_timecode(position)} / {format_timecode(duration)}"
        )

    def _on_video_finished(self) -> None:
        self._run_on_ui_thread(self._handle_video_finished)

    def _handle_video_finished(self) -> None:
        if self.video_player is None:
            return
        duration = self.video_player.duration
        self._update_time_label(duration, duration)
        self.timeline.set_playhead(duration)
        self._update_playhead()
        self.play_pause_button.configure(text="\u25B6 Play")
        self._set_status("Video playback finished.")

    def _on_video_error(self, message: str) -> None:
        self._run_on_ui_thread(lambda: self._handle_video_error(message))

    def _handle_video_error(self, message: str) -> None:
        self.play_pause_button.configure(text="\u25B6 Play")
        self._set_status(message)

    def _on_play_pause(self) -> None:
        if self.video_player is None:
            return
        if self.video_player.is_playing:
            self.video_player.pause()
            self.play_pause_button.configure(text="\u25B6 Play")
            self._set_status("Video paused.")
        else:
            self.video_player.play()
            self.play_pause_button.configure(text="\u23F8 Pause")
            self._set_status("Playing video...")

    def _on_stop_video(self) -> None:
        if self.video_player is None:
            return
        self.video_player.stop()
        self.play_pause_button.configure(text="\u25B6 Play")
        self._update_time_label(0.0, self.video_player.duration)
        self.seek_var.set(0.0)
        self.timeline.set_playhead(0.0)
        self._update_playhead()
        self._set_status("Video stopped.")

    def _on_seek_drag(self, _value: str) -> None:
        if self.video_player is None:
            return
        self._video_seek_pending = True
        target = float(self.seek_var.get())
        self.video_player.seek(target)
        self._update_time_label(target, self.video_player.duration)
        # Phase 5: seeking with the Phase 4 control also moves the
        # timeline playhead.
        self.timeline.set_playhead(target)
        self._update_playhead()
        # Allow tick updates to resume shortly after the seek settles.
        # Bug 2 (RC6): track the timer so a rapid second seek cancels
        # the first one (otherwise the first timer clears the pending
        # flag before the second seek has settled).
        self._cancel_after("_seek_after_id")
        self._seek_after_id = self.after(150, self._clear_seek_pending)

    def _clear_seek_pending(self) -> None:
        self._video_seek_pending = False
        self._seek_after_id = None  # timer has fired

    def _on_volume_change(self, _value: str) -> None:
        if self.video_player is None:
            return
        self.video_player.set_volume(float(self.volume_var.get()))

    def _on_mute_toggle(self) -> None:
        if self.video_player is None:
            return
        self.video_player.set_muted(bool(self.mute_var.get()))

    def _on_close(self) -> None:
        """Clean shutdown: stop all playback, then destroy the window."""
        try:
            self.after_cancel(self._poll_after_id)
        except Exception:
            pass
        stop_sound()  # Bug 1B: purge any async winsound playback
        self._cancel_after("_preview_after_id")
        self._cancel_after("_voice_after_id")
        self._teardown_video_player()
        self.destroy()

    def _build_preview(self, parent: ttk.Widget) -> None:
        # 3. Video Preview (Phase 4)
        preview_frame = ttk.LabelFrame(parent, text="Video Preview", padding=4)
        preview_frame.grid(row=0, column=1, sticky="nsew")
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(
            preview_frame,
            bg=PREVIEW_BG,
            highlightthickness=0,
        )
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        self.preview_canvas.bind("<Configure>", self._on_preview_resize)
        self._preview_placeholder_id = self.preview_canvas.create_text(
            0,
            0,
            text="Upload a video to preview it here",
            fill="#8a8a8a",
            font=("Segoe UI", 11),
            tags="placeholder",
        )
        self.preview_canvas.coords(
            "placeholder",
            self.preview_canvas.winfo_width() / 2,
            self.preview_canvas.winfo_height() / 2,
        )

        # --- Playback controls row ---
        controls = ttk.Frame(preview_frame)
        controls.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        controls.columnconfigure(2, weight=1)

        self.play_pause_button = ttk.Button(
            controls, text="\u25B6 Play", command=self._on_play_pause,
            state="disabled", width=10,
        )
        self.play_pause_button.grid(row=0, column=0, padx=(0, 4))

        self.stop_video_button = ttk.Button(
            controls, text="\u25A0 Stop", command=self._on_stop_video,
            state="disabled", width=10,
        )
        self.stop_video_button.grid(row=0, column=1, padx=(0, 8))

        # Seek slider
        self.seek_var = tk.DoubleVar(value=0.0)
        self.seek_slider = ttk.Scale(
            controls,
            from_=0.0,
            to=1.0,
            orient="horizontal",
            variable=self.seek_var,
            command=self._on_seek_drag,
            state="disabled",
        )
        self.seek_slider.grid(row=0, column=2, sticky="ew", padx=(0, 8))

        self.time_label = ttk.Label(
            controls, text="00:00 / 00:00", width=14, anchor="center"
        )
        self.time_label.grid(row=0, column=3, padx=(0, 8))

        # Volume + mute
        self.mute_var = tk.BooleanVar(value=False)
        self.mute_button = ttk.Checkbutton(
            controls, text="Mute", variable=self.mute_var,
            command=self._on_mute_toggle, state="disabled",
        )
        self.mute_button.grid(row=0, column=4, padx=(0, 4))

        self.volume_var = tk.DoubleVar(value=1.0)
        self.volume_slider = ttk.Scale(
            controls,
            from_=0.0,
            to=1.0,
            orient="horizontal",
            variable=self.volume_var,
            command=self._on_volume_change,
            length=90,
            state="disabled",
        )
        self.volume_slider.grid(row=0, column=5)

    def _build_timeline(self, parent: ttk.Widget) -> None:
        # 4. Timeline (Phase 5: ruler + VIDEO/AUDIO tracks + playhead)
        timeline_frame = ttk.LabelFrame(parent, text="Timeline", padding=4)
        timeline_frame.grid(row=1, column=1, sticky="nsew", pady=(8, 0))
        timeline_frame.rowconfigure(0, weight=1)
        timeline_frame.columnconfigure(0, weight=1)

        self.timeline_canvas = tk.Canvas(
            timeline_frame,
            bg=TIMELINE_BG,
            highlightthickness=1,
            highlightbackground="#cccccc",
        )
        self.timeline_canvas.grid(row=0, column=0, sticky="nsew")
        self.timeline_canvas.bind("<Configure>", self._on_timeline_resize)
        # Click / press handling (seek, or audio-clip move/trim).
        self.timeline_canvas.bind("<Button-1>", self._on_timeline_click)
        # Phase 8: hover feedback (resize cursor over the selected clip's
        # edges). <Motion> only fires while the pointer is over the canvas
        # and no button is held, so it never interferes with drags.
        self.timeline_canvas.bind("<Motion>", self._on_timeline_motion)

        self._redraw_timeline()

    # -------------------------------------------- Phase 5: timeline UI

    # Layout constants for the timeline canvas (pixels).
    _TL_LABEL_W = 56    # left column with the track names
    _TL_RULER_H = 22    # height of the time ruler strip
    _TL_VIDEO_H = 34    # height of the VIDEO track row
    _TL_AUDIO_H = 30    # height of the AUDIO track row
    _TL_GAP = 4         # vertical gap between rows
    _TL_PAD_RIGHT = 8   # right padding of the time area

    def _timeline_geometry(self) -> dict:
        """Compute the timeline layout in canvas pixels.

        Returns the time-area origin/width plus the vertical bounds of
        the ruler and the two tracks. Tiny windows are clamped to sane
        minimums so drawing never breaks while resizing.
        """
        canvas = self.timeline_canvas
        cw = max(canvas.winfo_width(), 200)
        ch = max(canvas.winfo_height(), 120)

        left = self._TL_LABEL_W
        width = max(cw - left - self._TL_PAD_RIGHT, 1)

        ruler_top = 0
        ruler_bottom = self._TL_RULER_H
        video_top = ruler_bottom + self._TL_GAP
        video_bottom = video_top + self._TL_VIDEO_H
        audio_top = video_bottom + self._TL_GAP
        audio_bottom = audio_top + self._TL_AUDIO_H

        return {
            "left": left,
            "width": width,
            "ruler_top": ruler_top,
            "ruler_bottom": ruler_bottom,
            "video_top": video_top,
            "video_bottom": video_bottom,
            "audio_top": audio_top,
            "audio_bottom": audio_bottom,
            "canvas_w": cw,
            "canvas_h": ch,
        }

    def _redraw_timeline(self) -> None:
        """Full redraw of ruler, tracks and playhead.

        Lightweight: only rectangles/lines/text from timeline state.
        No video decoding happens here.
        """
        canvas = self.timeline_canvas
        canvas.delete("all")
        g = self._timeline_geometry()
        left, width = g["left"], g["width"]

        # --- Time ruler ---
        canvas.create_rectangle(
            0, g["ruler_top"], g["canvas_w"], g["ruler_bottom"],
            fill=RULER_BG, outline="", tags="ruler",
        )
        canvas.create_line(
            left, g["ruler_bottom"], left + width, g["ruler_bottom"],
            fill=RULER_LINE, tags="ruler",
        )
        for tick in self.timeline.ruler_tick_times():
            x = self.timeline.time_to_x(tick, left, width)
            canvas.create_line(
                x, g["ruler_top"] + 8, x, g["ruler_bottom"],
                fill=RULER_LINE, tags="ruler",
            )
            canvas.create_text(
                x + 3, g["ruler_top"] + 2,
                text=format_ruler_label(tick),
                anchor="nw", fill=RULER_TEXT,
                font=("Segoe UI", 8), tags="ruler",
            )

        # --- Track labels ---
        video_mid = (g["video_top"] + g["video_bottom"]) / 2
        audio_mid = (g["audio_top"] + g["audio_bottom"]) / 2
        canvas.create_text(
            6, video_mid, text="VIDEO", anchor="w",
            fill=TRACK_LABEL, font=("Segoe UI", 8, "bold"),
        )
        canvas.create_text(
            6, audio_mid, text="AUDIO", anchor="w",
            fill=TRACK_LABEL, font=("Segoe UI", 8, "bold"),
        )

        # --- VIDEO track ---
        clip = self.timeline.video_clip
        if clip is None:
            canvas.create_rectangle(
                left, g["video_top"], left + width, g["video_bottom"],
                fill=AUDIO_TRACK_FILL, outline=AUDIO_TRACK_EDGE,
                dash=(3, 3), tags="video_track",
            )
            canvas.create_text(
                left + width / 2, video_mid,
                text="Upload a video to see it on the timeline",
                fill=AUDIO_PLACEHOLDER_TEXT, font=("Segoe UI", 9),
                tags="video_track",
            )
        else:
            x0 = self.timeline.time_to_x(clip.start, left, width)
            x1 = self.timeline.time_to_x(
                clip.start + clip.duration, left, width
            )
            canvas.create_rectangle(
                x0, g["video_top"], x1, g["video_bottom"],
                fill=VIDEO_CLIP_FILL, outline=VIDEO_CLIP_EDGE,
                tags="video_track",
            )
            # Show the filename when the block is wide enough.
            max_chars = int((x1 - x0 - 12) / 6)
            if max_chars >= 5:
                name = clip.source.name
                if len(name) > max_chars:
                    name = name[: max_chars - 1] + "\u2026"
                canvas.create_text(
                    (x0 + x1) / 2, video_mid, text=name,
                    fill=VIDEO_CLIP_TEXT, font=("Segoe UI", 8),
                    tags="video_track",
                )

        # --- AUDIO track ---
        canvas.create_rectangle(
            left, g["audio_top"], left + width, g["audio_bottom"],
            fill=AUDIO_TRACK_FILL, outline=AUDIO_TRACK_EDGE,
            tags="audio_track",
        )
        if not self.timeline.audio_clips:
            canvas.create_text(
                left + width / 2, audio_mid,
                text="Drag audio from the Assets panel onto this track",
                fill=AUDIO_PLACEHOLDER_TEXT, font=("Segoe UI", 9),
                tags="audio_track",
            )
        for index, aclip in enumerate(self.timeline.audio_clips):
            ax0 = self.timeline.time_to_x(aclip.start, left, width)
            ax1 = self.timeline.time_to_x(aclip.start + aclip.duration, left, width)
            if ax1 - ax0 < 6:
                ax1 = ax0 + 6  # keep over-running clips visible at the edge
            if index == self.timeline.selected_audio:
                fill = AUDIO_CLIP_SELECTED_FILL
                edge = AUDIO_CLIP_SELECTED_EDGE
                outline_w = 2
            else:
                fill = AUDIO_CLIP_FILL
                edge = AUDIO_CLIP_EDGE
                outline_w = 1
            canvas.create_rectangle(
                ax0, g["audio_top"], ax1, g["audio_bottom"],
                fill=fill, outline=edge, width=outline_w,
                tags=("audio_track", "audio_clip"),
            )
            max_chars = int((ax1 - ax0 - 12) / 6)
            if max_chars >= 5:
                name = aclip.source.name
                if len(name) > max_chars:
                    name = name[: max_chars - 1] + "\u2026"
                canvas.create_text(
                    (ax0 + ax1) / 2, audio_mid, text=name,
                    fill=AUDIO_CLIP_TEXT, font=("Segoe UI", 8),
                    tags=("audio_track", "audio_clip"),
                )
            # Phase 8: trim handles on the selected clip's edges. Tagged
            # "clip_handle" (NOT "audio_clip") so Phase 7 canvas-fill
            # assertions are unaffected.
            if index == self.timeline.selected_audio:
                for hx in (ax0, ax1):
                    canvas.create_rectangle(
                        hx - CLIP_HANDLE_W / 2, g["audio_top"] + 1,
                        hx + CLIP_HANDLE_W / 2, g["audio_bottom"] - 1,
                        fill=AUDIO_CLIP_SELECTED_EDGE, outline="",
                        tags=("audio_track", "clip_handle"),
                    )

        self._update_playhead()

    def _update_playhead(self) -> None:
        """Move the playhead to ``timeline.playhead`` (cheap update)."""
        canvas = self.timeline_canvas
        g = self._timeline_geometry()
        x = self.timeline.time_to_x(
            self.timeline.playhead, g["left"], g["width"]
        )
        top = g["ruler_top"]
        bottom = g["audio_bottom"]
        if canvas.find_withtag("playhead_line"):
            canvas.coords("playhead_line", x, top, x, bottom)
            canvas.coords(
                "playhead_cap", x - 5, top, x + 5, top, x, top + 7
            )
        else:
            canvas.create_line(
                x, top, x, bottom, fill=PLAYHEAD_COLOR, width=2,
                tags="playhead_line",
            )
            canvas.create_polygon(
                x - 5, top, x + 5, top, x, top + 7,
                fill=PLAYHEAD_COLOR, outline="", tags="playhead_cap",
            )

    def _on_timeline_resize(self, _event: tk.Event) -> None:
        """Window resizing keeps the timeline usable (full redraw)."""
        self._redraw_timeline()

    def _on_timeline_click(self, event: tk.Event) -> None:
        """Press on timeline: start an audio-clip drag or seek the video.

        - Press over an AudioClip on the AUDIO track -> select it and
          begin a move drag.
        - Phase 8: press near the left/right edge of an ALREADY SELECTED
          clip -> begin a trim drag instead of a move.
        - Press anywhere else (ruler, VIDEO track, empty AUDIO track) ->
          deselect any selected audio clip (Phase 7) and seek.
        """
        g = self._timeline_geometry()
        # Audio-clip hit test (must be within the AUDIO track band and the
        # timeline's horizontal extent).
        if (
            g["audio_top"] <= event.y <= g["audio_bottom"]
            and g["left"] <= event.x <= g["left"] + g["width"]
        ):
            seconds = self.timeline.x_to_time(event.x, g["left"], g["width"])
            index = self.timeline.audio_clip_at(
                seconds, g["audio_top"], g["audio_bottom"]
            )
            if index is not None:
                # Phase 8: an edge press on an ALREADY SELECTED clip grabs
                # that edge for trimming. The "already selected" check must
                # happen BEFORE select_audio() below; selection gating keeps
                # the Phase 7 behavior (first press = select + move) intact.
                was_selected = self.timeline.selected_audio == index
                self.timeline.select_audio(index)
                clip = self.timeline.audio_clips[index]
                edge = (
                    self._edge_under_pointer(event.x, clip, g)
                    if was_selected
                    else None
                )
                if edge is not None:
                    self._drag = {"kind": "trim", "index": index, "edge": edge}
                    self._redraw_timeline()
                    self._set_status(
                        f"Trimming {clip.source.name}: drag the {edge} edge "
                        f"to shorten the clip."
                    )
                    return
                self._drag = {
                    "kind": "clip",
                    "index": index,
                    "offset": seconds - clip.start,
                }
                self._redraw_timeline()
                self._set_status(
                    f"Selected audio clip: {clip.source.name} "
                    f"({format_timecode(clip.duration)}) at "
                    f"{format_timecode(clip.start)}."
                )
                return

        # Phase 7: any press that is not on an audio clip deselects the
        # currently selected clip (empty AUDIO track, ruler, VIDEO track).
        if self.timeline.selected_audio is not None:
            self.timeline.select_audio(None)
            self._redraw_timeline()

        if self.timeline.duration <= 0.0:
            return  # empty timeline: nothing to seek
        target = self.timeline.x_to_time(event.x, g["left"], g["width"])
        target = self.timeline.set_playhead(target)
        self._update_playhead()
        self._update_time_label(target, self.timeline.duration)
        if self.video_player is not None:
            self._video_seek_pending = True
            self.seek_var.set(target)
            self.video_player.seek(target)
            # Bug 2 (RC6): single tracked timer (see _on_seek_drag).
            self._cancel_after("_seek_after_id")
            self._seek_after_id = self.after(150, self._clear_seek_pending)
        self._set_status(f"Timeline seeked to {format_timecode(target)}.")

    def _edge_under_pointer(self, x: float, clip, g: dict) -> Optional[str]:
        """Phase 8: return "left"/"right" if ``x`` is within EDGE_GRAB_PX
        of ``clip``'s edge (in canvas pixels), else None.

        Only meaningful for the selected clip (callers gate on that).
        """
        left_x = self.timeline.time_to_x(clip.start, g["left"], g["width"])
        right_x = self.timeline.time_to_x(
            clip.start + clip.duration, g["left"], g["width"]
        )
        if abs(x - left_x) <= EDGE_GRAB_PX:
            return "left"
        if abs(x - right_x) <= EDGE_GRAB_PX:
            return "right"
        return None

    def _on_timeline_motion(self, event: tk.Event) -> None:
        """Phase 8: hover feedback — resize cursor over the selected
        clip's edges, normal cursor everywhere else.

        Bound to <Motion> on the timeline canvas, which only fires while
        the pointer is over the canvas and no button is held, so this
        never interferes with drags.
        """
        g = self._timeline_geometry()
        cursor = ""
        index = self.timeline.selected_audio
        if (
            index is not None
            and 0 <= index < len(self.timeline.audio_clips)
            and g["audio_top"] <= event.y <= g["audio_bottom"]
            and g["left"] <= event.x <= g["left"] + g["width"]
        ):
            clip = self.timeline.audio_clips[index]
            if self._edge_under_pointer(event.x, clip, g) is not None:
                cursor = "sb_h_double_arrow"
        self.timeline_canvas.config(cursor=cursor)

    def _on_asset_drag_start(self, wav_path: Path) -> None:
        """Begin an audio-asset drag from the Media panel (Phase 6).

        The actual drop is resolved by the app-wide motion/release handlers
        below, so a click that never leaves the panel can simply cancel.
        """
        self._drag = {"kind": "media", "path": Path(wav_path)}
        self._set_status(f"Drag {wav_path.name} onto the AUDIO track\u2026")

    def _pointer_on_canvas(self, event: tk.Event) -> tuple:
        """Return (xc, yc) canvas coordinates for a global event, or None.

        Uses root-relative coordinates so it works for events delivered to
        any widget via ``bind_all``.
        """
        canvas = self.timeline_canvas
        xc = event.x_root - canvas.winfo_rootx()
        yc = event.y_root - canvas.winfo_rooty()
        return xc, yc

    def _timeline_x_in_bounds(self, g: dict, xc: float) -> float:
        """Clamp an x canvas coordinate to the timeline drawing extent."""
        return min(max(xc, g["left"]), g["left"] + g["width"])

    def _on_global_drag_motion(self, event: tk.Event) -> None:
        """During an audio drag, show a drop hint / move the clip live."""
        if self._drag is None:
            return
        g = self._timeline_geometry()
        xc, yc = self._pointer_on_canvas(event)
        over_audio = (
            g["left"] <= xc <= g["left"] + g["width"]
            and g["audio_top"] <= yc <= g["audio_bottom"]
        )

        if self._drag["kind"] == "clip":
            seconds = self.timeline.x_to_time(xc, g["left"], g["width"])
            new_start = seconds - self._drag["offset"]
            self.timeline.move_audio_clip(self._drag["index"], new_start)
            self._redraw_timeline()
            actual = self.timeline.audio_clips[self._drag["index"]].start
            self._set_status(
                f"Moving audio clip to {format_timecode(actual)}."
            )
            return

        if self._drag["kind"] == "trim":
            # Phase 8: live trim. The model clamps the new edge into the
            # shrink-only range, so dragging outwards is a harmless no-op.
            seconds = self.timeline.x_to_time(xc, g["left"], g["width"])
            self.timeline.trim_audio_clip(
                self._drag["index"], self._drag["edge"], seconds
            )
            self._redraw_timeline()
            clip = self.timeline.audio_clips[self._drag["index"]]
            self._set_status(
                f"Trimming audio clip: {format_timecode(clip.start)}\u2013"
                f"{format_timecode(clip.start + clip.duration)}."
            )
            return

        # kind == "media": draw/update a small drop-hint on the audio track.
        canvas = self.timeline_canvas
        canvas.delete("drop_hint")
        if over_audio:
            x = self._timeline_x_in_bounds(g, xc)
            canvas.create_line(
                x, g["audio_top"] + 2, x, g["audio_bottom"] - 2,
                fill=AUDIO_CLIP_SELECTED_EDGE, width=2, dash=(3, 2),
                tags="drop_hint",
            )

    def _on_global_drag_release(self, event: tk.Event) -> None:
        """Resolve an in-progress audio drag (drop or move)."""
        if self._drag is None:
            return
        drag = self._drag
        self._drag = None
        self.timeline_canvas.delete("drop_hint")

        if drag["kind"] == "media":
            g = self._timeline_geometry()
            xc, yc = self._pointer_on_canvas(event)
            over_audio = (
                g["left"] <= xc <= g["left"] + g["width"]
                and g["audio_top"] <= yc <= g["audio_bottom"]
            )
            if not over_audio:
                self._set_status("Audio drop cancelled (not over the AUDIO track).")
                return
            try:
                duration = probe_wav_duration(drag["path"])
            except OSError as exc:
                self._set_status(f"Could not read WAV: {exc}")
                return
            # Phase 7: reject WAVs longer than the timeline (no partial
            # clips are ever created).
            if self.timeline.duration > 0 and duration > self.timeline.duration:
                self._set_status(
                    f"Cannot add {drag['path'].name}: the WAV "
                    f"({format_timecode(duration)}) is longer than the "
                    f"timeline ({format_timecode(self.timeline.duration)})."
                )
                return
            start = self.timeline.x_to_time(xc, g["left"], g["width"])
            clip = self.timeline.add_audio_clip(drag["path"], start, duration)
            self._redraw_timeline()
            if clip.start != start:
                self._set_status(
                    f"Added audio clip {drag['path'].name} "
                    f"({format_timecode(duration)}) at "
                    f"{format_timecode(clip.start)} "
                    f"(clamped to fit the timeline)."
                )
            else:
                self._set_status(
                    f"Added audio clip {drag['path'].name} "
                    f"({format_timecode(duration)}) at "
                    f"{format_timecode(start)}."
                )
        elif drag["kind"] == "trim":
            # Phase 8: report the final trimmed range.
            self._redraw_timeline()
            idx = drag["index"]
            if 0 <= idx < len(self.timeline.audio_clips):
                clip = self.timeline.audio_clips[idx]
                self._set_status(
                    f"Trimmed audio clip to {format_timecode(clip.start)}\u2013"
                    f"{format_timecode(clip.start + clip.duration)}."
                )
        else:  # kind == "clip"
            self._redraw_timeline()
            idx = drag["index"]
            if 0 <= idx < len(self.timeline.audio_clips):
                start = self.timeline.audio_clips[idx].start
                self._set_status(
                    f"Audio clip moved to {format_timecode(start)}."
                )

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar()
        status_bar = ttk.Label(
            self,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
            padding=(6, 3),
        )
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
