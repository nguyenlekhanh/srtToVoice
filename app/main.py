"""Phase 1 — Voice Library scanner for the local video editor.

Tkinter UI with five sections: Media, Voice, Video Preview, Timeline,
Export. Only the Voice section is functional in Phase 1: it scans the
``voices/`` directory recursively for Piper ``.onnx`` + ``.onnx.json``
pairs, shows their metadata, and lets the user select a voice.

No audio is generated yet. Media, Video Preview, Timeline and Export
remain placeholders.

Run with the project's .venv:
    .venv\\Scripts\\python.exe -m app.main
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import List, Optional

from app.voice_library import (
    NO_VOICES_MESSAGE,
    Voice,
    filter_voices,
    scan_voices,
)

APP_TITLE = "Local Video Editor — Phase 1 (Voice Library)"
COMING_SOON = "Coming in next phases"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VOICES_ROOT = PROJECT_ROOT / "voices"

BG = "#f2f2f2"
PANEL_BG = "#ffffff"
PREVIEW_BG = "#1e1e1e"


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

        self._build_layout()

        self._refresh_voices(initial=True)

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

        # 1. Media (unchanged placeholder)
        media = self._section(sidebar, "Media")
        self._coming_soon_button(media, "Upload Video").pack(
            fill=tk.X, pady=2
        )
        self._coming_soon_button(media, "Upload SRT").pack(fill=tk.X, pady=2)

        # 2. Voice Library (Phase 1)
        self._build_voice_section(sidebar)

        # 5. Export (unchanged placeholder)
        export = self._section(sidebar, "Export")
        self._coming_soon_button(export, "Export").pack(fill=tk.X, pady=2)

        note = ttk.Label(
            sidebar,
            text="Media, Preview, Timeline and Export\nare placeholders for now.",
            foreground="#666666",
        )
        note.pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------- Voice section

    def _build_voice_section(self, parent: ttk.Widget) -> None:
        voice = ttk.LabelFrame(parent, text="Voice Library", padding=8)
        voice.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        voice.columnconfigure(0, weight=1)
        voice.rowconfigure(3, weight=1)

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

        # Scrollable voice list
        list_frame = ttk.Frame(voice)
        list_frame.grid(row=3, column=0, sticky="nsew")
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

    def _build_preview(self, parent: ttk.Widget) -> None:
        # 3. Video Preview
        preview_frame = ttk.LabelFrame(parent, text="Video Preview", padding=4)
        preview_frame.grid(row=0, column=1, sticky="nsew")

        canvas = tk.Canvas(
            preview_frame,
            bg=PREVIEW_BG,
            highlightthickness=0,
        )
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.bind(
            "<Configure>",
            lambda e: canvas.coords("text", e.width / 2, e.height / 2),
        )
        canvas.create_text(
            0,
            0,
            text="Video preview — coming in next phases",
            fill="#8a8a8a",
            font=("Segoe UI", 11),
            tags="text",
        )

    def _build_timeline(self, parent: ttk.Widget) -> None:
        # 4. Timeline
        timeline_frame = ttk.LabelFrame(parent, text="Timeline", padding=4)
        timeline_frame.grid(row=1, column=1, sticky="nsew", pady=(8, 0))

        canvas = tk.Canvas(
            timeline_frame,
            bg=PANEL_BG,
            highlightthickness=1,
            highlightbackground="#cccccc",
        )
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.bind(
            "<Configure>",
            lambda e: canvas.coords("text", e.width / 2, e.height / 2),
        )
        canvas.create_text(
            0,
            0,
            text="Timeline — coming in next phases",
            fill="#8a8a8a",
            font=("Segoe UI", 11),
            tags="text",
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
