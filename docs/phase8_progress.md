# Phase 8 Progress — Audio Clip Trimming

> Persistent source of truth for the Phase 8 implementation state.
>
> **Resume protocol:** at the start of every implementation step read this
> file, compare it against `git diff` and the current sources, and continue
> from the recorded state. If this file and the sources disagree,
> investigate and correct this file before proceeding. Do not rely on
> conversation history.

## 1. Phase 8 goal

Let users trim audio clips on the timeline by dragging the left or right
edge of an audio clip rectangle. Extends the Phase 7 editing workflow
(move / select / clamp) while preserving the non-destructive,
metadata-only model. The underlying WAV files are never touched.

## 2. Approved scope

- Drag the **left edge** of an audio clip to trim its start (right edge
  stays fixed).
- Drag the **right edge** of an audio clip to trim its end (left edge
  stays fixed).
- Minimum clip duration `MIN_AUDIO_CLIP_DURATION = 0.1` s.
- Trims clamped to the timeline bounds `[0, duration]`.
- Live feedback while dragging (clip resizes, status bar shows range).
- Resize cursor (`sb_h_double_arrow`) when hovering near a clip edge
  (`EDGE_GRAB_PX` pixel threshold).
- Clip-body drag (move) unchanged.
- Status message on release: `Trimmed audio clip to MM:SS–MM:SS.`
- Model stays UI-free (`app/timeline.py`: no Tkinter, no file access).

## 3. Explicit exclusions (must NOT be touched)

- No trimming of the VIDEO clip.
- No splitting, snapping, transitions, effects.
- No undo/redo.
- No audio mixing or playback of timeline clips.
- No export/rendering (Export stays a placeholder).
- No project save/load.
- No changes to `app/voice_library.py`, `app/voice_preview.py`,
  `app/srt_voice.py`, `app/video_preview.py`.
- No new dependencies, no FFmpeg, no subprocess changes.
- No weakening of existing Phase 1–7 tests.
- No commit / push until explicitly instructed.

## 4. Files planned to change

| File | Change |
|------|--------|
| `docs/phase8_progress.md` | This file (new). |
| `app/timeline.py` | `MIN_AUDIO_CLIP_DURATION`, `Timeline.trim_audio_clip()`, docstrings. |
| `app/main.py` | Edge-grab detection, trim drag (motion/release), hover cursor, clip edge handles, `APP_TITLE`, module docstring. |
| `tests/test_timeline_model.py` | New `AudioClipTrimTests` class. |
| `tests/test_timeline_ui.py` | New `TrimClipTests` class. |
| `tests/__init__.py` | Docstring bump to Phase 8. |
| `README.md` | Phase 8 section, status, roadmap, folder structure. |

## 5. Implementation checklist

- [x] Create `docs/phase8_progress.md` with approved plan
- [x] `app/timeline.py`: `MIN_AUDIO_CLIP_DURATION` + `trim_audio_clip()`
      (found already implemented in the working tree on resume — see §7)
- [x] Baseline-equivalent: full Phase 1–7 suite passes with the model
      change present (true pre-change baseline no longer possible; the
      model change is additive and must not break any Phase 1–7 test)
      — 60 tests OK, see §9
- [x] Model tests `AudioClipTrimTests` added + passing — 15 tests, see §9
- [x] `app/main.py`: edge-grab detection in `_on_timeline_click`
      (`was_selected` gating + `_edge_under_pointer` helper)
- [x] `app/main.py`: trim drag in `_on_global_drag_motion` / `_on_global_drag_release`
      (kind "trim"; release message "Trimmed audio clip to MM:SS–MM:SS.")
- [x] `app/main.py`: hover cursor feedback (`_on_timeline_motion`)
      — `<Motion>` binding ALREADY ADDED but the method did not exist
      yet; method now implemented (`sb_h_double_arrow` over the selected
      clip's edges, "" elsewhere)
- [x] `app/main.py`: clip edge handles in `_redraw_timeline`
      (tag `clip_handle`, found in working tree on resume)
- [x] `app/main.py`: Phase 8 module docstring, `APP_TITLE`,
      `EDGE_GRAB_PX`/`CLIP_HANDLE_W` constants, `_drag` comment
      (found in working tree on resume)
- [x] UI tests `TrimClipTests` added + passing — 11 tests, see §9
- [x] README + `tests/__init__.py` updated
      (README: title/intro/status bumped to Phase 8, new "Timeline +
      Audio Clip Trimming (Phases 5–8)" section, folder structure adds
      `app/timeline.py` + `tests/`, roadmap refreshed; `tests/__init__.py`
      docstring -> Phase 8)
- [x] Full regression suite passes — 86 tests OK, see §9
- [x] Final progress-file update

## 6. Current status

COMPLETE — Phase 8 audio clip trimming is fully implemented and verified.
Model layer (`trim_audio_clip`), all UI handlers (edge grab, trim drag,
hover cursor, edge handles), model + UI tests, README and
`tests/__init__.py` are all done. Full regression suite passes (86 tests).
No commit / push has been made (per §3).

## 7. Completed work

- `docs/phase8_progress.md` created with the approved plan.
- `app/timeline.py` (found done on resume, verified against `git diff`):
  - Module docstring bumped "Phases 5–7" -> "Phases 5–8", trimming scope
    bullet added, "NOT implemented" list updated (video trimming only).
  - `MIN_AUDIO_CLIP_DURATION = 0.1` module constant.
  - `AudioClip` docstring mentions Phase 8 trimming.
  - `Timeline.trim_audio_clip(index, edge, new_time) -> None`:
    right edge clamps new end into `[start + MIN, min(end, duration)]`;
    left edge clamps new start into `[start, end - MIN]` additionally
    capped at the timeline duration; invalid index/edge/time and
    NaN/inf are no-ops; degenerate `lo > hi` bounds are no-ops.
    Resulting duration guarded with `max(MIN_AUDIO_CLIP_DURATION, ...)`
    on both edges (float-rounding fix, see §10).
- `app/main.py` (UI layer):
  - Module docstring Phase 8 paragraph; `APP_TITLE` -> Phase 8.
  - Constants `EDGE_GRAB_PX = 6`, `CLIP_HANDLE_W = 4.0`.
  - `_drag` comment documents the new `{"kind": "trim", ...}` shape.
  - `<Motion>` bound to `_on_timeline_motion` (hover cursor feedback).
  - `_edge_under_pointer(x, clip, g)` helper: returns "left"/"right" if
    the pointer is within `EDGE_GRAB_PX` of a clip edge, else None.
  - `_on_timeline_click`: edge-grab detection gated on the clip being
    ALREADY selected (`was_selected` checked before `select_audio`), so
    the first press on an unselected clip is still a Phase 7 move.
  - `_on_timeline_motion`: `sb_h_double_arrow` cursor over the selected
    clip's edges, `""` elsewhere.
  - `_on_global_drag_motion`: `kind == "trim"` branch calls
    `trim_audio_clip` live and reports the current range.
  - `_on_global_drag_release`: `kind == "trim"` branch reports
    `Trimmed audio clip to MM:SS–MM:SS.`
  - `_redraw_timeline`: draws two `clip_handle` rectangles on the
    selected clip's edges (tag `clip_handle`, NOT `audio_clip`).
- `tests/test_timeline_model.py`: `AudioClipTrimTests` (15 tests).
- `tests/test_timeline_ui.py`: `TrimClipTests` (11 tests).
- `tests/__init__.py`: docstring bumped to Phase 8.
- `README.md`: title/intro/status bumped to Phase 8, new "Timeline +
  Audio Clip Trimming (Phases 5–8)" section, folder structure adds
  `app/timeline.py` + `tests/`, roadmap refreshed.

## 8. Current work in progress

None — Phase 8 is complete. All checklist items in §5 are done and the
full regression suite passes.

## 9. Tests completed and their results

- Baseline-equivalent run (model change already present):
  - Command: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
  - Result: `Ran 60 tests in 3.147s — OK` (all Phase 1–7 tests pass;
    the model change is additive and broke nothing).
- Model trim tests:
  - Command: `.venv\Scripts\python.exe -m unittest tests.test_timeline_model -v`
  - Result: `Ran 54 tests in 0.027s — OK` (39 Phase 1–7 model tests +
    15 new `AudioClipTrimTests`).
- UI regression after all `app/main.py` handler work:
  - Command: `.venv\Scripts\python.exe -m unittest tests.test_timeline_ui -v`
  - Result: `Ran 21 tests in 4.983s — OK` (all Phase 6–7 UI tests pass
    unmodified with the new trim handlers in place).
- UI trim tests:
  - Command: `.venv\Scripts\python.exe -m unittest tests.test_timeline_ui.TrimClipTests -v`
  - Result: `Ran 11 tests in 2.220s — OK` (all 11 new `TrimClipTests`
    passed on the first run: shrink both edges, never-extend both edges,
    minimum duration, release status message, first-press-is-move gating,
    hover cursor on/off, edge handles drawn/not drawn).
- FINAL full regression suite:
  - Command: `.venv\Scripts\python.exe -m unittest discover -s tests`
  - Result: `Ran 86 tests in 9.470s — OK` (60 Phase 1–7 tests + 15 model
    trim tests + 11 UI trim tests; nothing broken).
  - Import sanity check: `import app.main; import app.timeline` -> OK,
    `APP_TITLE = "Local Video Editor — Phase 8 (Audio Clip Trimming)"`.

## 10. Failures encountered and how they were fixed

- `AudioClipTrimTests` first run: 2 failures
  (`test_trim_right_edge_respects_minimum_duration`,
  `test_trim_minimum_duration_clip_is_noop`):
  `AssertionError: 0.10000000000000009 != 0.1`.
  - Root cause: float rounding in `trim_audio_clip` — computing the
    duration as `(start + MIN) - start` yields `0.10000000000000009`
    (rounds UP), and symmetrically `end - (end - MIN)` can round DOWN
    below MIN.
  - Implementation fix: `app/timeline.py` `trim_audio_clip` now guards
    the resulting duration with
    `max(MIN_AUDIO_CLIP_DURATION, ...)` on both edges, so the invariant
    "duration never below MIN" holds exactly.
  - Test fix: the two new Phase 8 tests asserted float-exact equality
    against MIN; changed to `assertGreaterEqual(duration, MIN)` (the
    hard invariant) + `assertAlmostEqual` (matches the existing
    float-comparison style in the file). No Phase 1–7 test was touched.
  - Rerun: `Ran 54 tests — OK`.

## 11. Remaining work

None — Phase 8 is complete. Only outstanding action is the commit/push,
which is deliberately deferred until explicitly instructed (§3).

## 12. Verification status

- Baseline (pre-change) suite: NOT POSSIBLE — model change was already
  in the working tree when this session resumed. Substitute: full suite
  passed with the model change present before any further edits
  (60 tests OK, §9).
- Final suite: `Ran 86 tests in 9.470s — OK` (§9). All Phase 1–7 tests
  pass unmodified; no existing test was weakened.
- No commit / push has been made. `git status` shows the Phase 8 changes
  as unstaged modifications plus the untracked `docs/` folder.

## 13. Important implementation decisions

- **Trimming only SHRINKS clips.** Dragging an edge outwards never extends
  a clip beyond its original range. Rationale: `AudioClip` stores no
  in/out-point into the source WAV, so "restoring" trimmed material is not
  representable in the model; the approved plan describes trimming as
  shortening only.
  - Right edge: `new_end ∈ [start + MIN, min(original_end, timeline_duration)]`.
  - Left edge: `new_start ∈ [original_start, end − MIN]`, additionally
    capped at the timeline duration for over-running clips so the Phase 7
    invariant "start within [0, duration]" holds.
- `trim_audio_clip(index, edge, new_time) -> None` mirrors the void,
  no-op-on-invalid-index style of `move_audio_clip()`.
- `EDGE_GRAB_PX` must be small enough that the Phase 7 `DragClipTests`
  presses (0.05 s inside the clip edge) remain body-drags. Value chosen
  after measuring real canvas geometry (see §10/§13 updates).
- Trim drag reuses the existing `self._drag` machinery with a new kind:
  `{"kind": "trim", "index": int, "edge": "left"|"right"}`.
- Edge grab handles drawn with tag `clip_handle` (NOT `audio_clip`) so
  Phase 7 canvas-fill assertions are unaffected.

## 14. Known limitations

- Over-running clips (longer than the timeline) cannot be created via the
  UI (Phase 7 rejects them on drop); the model still handles them
  gracefully in `trim_audio_clip`.
- Edge grab only triggers when the pointer is ON the clip within
  `EDGE_GRAB_PX` of an edge (hit-test is inclusive at clip bounds);
  pressing just outside a clip edge seeks instead — hover cursor matches
  this exactly.
- **Trim is gated on the clip already being selected (Phase 8 decision).**
  Measured test-environment geometry: canvas time width = 136 px for a
  10 s timeline = 13.6 px/s, so the Phase 7 `DragClipTests` press
  (0.05 s inside the clip edge) is only ~0.68 px from the edge. Any
  unconditional pixel edge-zone (even 1 px) would have turned those
  Phase 7 move-drags into trims. With selection gating: first press on a
  clip = select + move (Phase 7 behavior, unchanged); dragging an edge
  of an already-selected clip = trim. Hover cursor shows the resize
  cursor only over the edges of the selected clip, so the interaction is
  discoverable. All Phase 7 tests pass unmodified.
- Pre-existing oddity (NOT touched, Phase 7 leftover):
  `tests/test_timeline_model.py` contains a stray
  `test_move_longer_than_timeline_clip_pins_to_zero` defined inside the
  `if __name__ == "__main__":` block after `unittest.main()` — dead code,
  never collected by test discovery. Left as-is to avoid altering
  Phase 7 tests.

## 15. Last updated

2026-08-21 (final) — Phase 8 COMPLETE. All UI handlers implemented
(`_on_timeline_motion`, edge-grab in `_on_timeline_click`, trim drag in
`_on_global_drag_motion`/`_on_global_drag_release`), `TrimClipTests`
(11 tests) added and passing, README + `tests/__init__.py` updated, and
the full regression suite passes (86 tests OK). No commit / push made.
