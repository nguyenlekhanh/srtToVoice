# Part 1 Regression — Voice-only fixes

Source of truth for the Part 1 debugging session. Another model can open
a new chat and resume exactly where this work stopped by reading this file.

Scope: voice-only regressions. Video/timeline behavior must NOT change.

- Bug 1A: missing/incorrect inter-cue voice timing/duration in generated SRT voice.
- Bug 1B: Stop Voice does not cancel active playback.

Rules:
- Do not weaken or remove existing tests.
- Real Piper timing verification must not be faked/mocked.
- If the approved minimal Bug 1A fix does not solve the timing problem,
  record the failure and STOP (no alternative fix, no Bug 1B).
- Do not commit or push.

---

## Bug 1A — inter-cue pauses missing in generated voice

Status: PASS

### Root cause (confirmed)

- `parse_srt_text` (`app/srt_voice.py`) joins SRT cue texts with blank
  lines (`"\n\n"`).
- Piper CLI (`.venv/Lib/site-packages/piper/__main__.py`) reads the input
  file line by line, strips and SKIPS blank lines, and synthesizes each
  remaining line separately. In `lines_to_wav()` silence bytes are only
  written BETWEEN sentence chunks of ONE input line (`if i > 0`), never
  between lines.
- `--sentence-silence` defaults to `0.0`, so even within one line no
  silence bytes are written unless the flag is passed.
- Net effect: every SRT cue becomes its own Piper input line -> cues are
  concatenated with ZERO silence between them in the generated WAV.
- The preview text ("Hey! Look at this! This is so cool!") is one line
  with several sentences, which is why the preview sounds paced but the
  generated SRT voice does not.
- Confirmed empirically with `_tmp_inspect.py` (duration delta only
  appears with `--sentence-silence` on single-line input).

### Goal

Restore an audible pause (~0.5 s) between SRT cues in the generated WAV
without changing anything else: still exactly ONE WAV, same Piper CLI
subprocess, same atomic write/cleanup behavior, no timeline changes.

### Implementation plan (approved)

1. `app/srt_voice.py::parse_srt_text` — join cue texts with a single
   space instead of blank lines, so all cues become separate sentences
   within ONE Piper input line (espeak splits on cue punctuation).
2. `app/srt_voice.py::generate_voice_wav` — add module constant
   `SENTENCE_SILENCE_SECONDS = 0.5` and pass
   `--sentence-silence 0.5` to the Piper command, so Piper writes real
   silence bytes between the sentence chunks.
3. Add deterministic tests in `tests/test_srt_voice.py` (no Piper
   subprocess, no audio hardware):
   - multi-cue SRT -> single-line, space-joined narration text
   - Piper command includes `--sentence-silence 0.5`
   - temp input file written for Piper contains the space-joined text
4. Live verification with real Piper (`voices/en_US-amy-medium.onnx`),
   driven through the real app code path (`parse_srt_text` +
   `generate_voice_wav`) by `_tmp_verify_1a.py`: measure WAV duration
   BEFORE and AFTER the fix. Expect after ≈ before + 1.0 s
   (3 cues -> 2 gaps × 0.5 s).

### Files expected to change

- `app/srt_voice.py` (`parse_srt_text`, `generate_voice_wav`)
- `tests/test_srt_voice.py` (new)
- `docs/regression_part1_voice.md` (this file)

### Log

- [x] Baseline (before-fix) WAV duration measured with real Piper
- [x] `parse_srt_text` space-join implemented
- [x] `generate_voice_wav` `--sentence-silence 0.5` implemented
- [x] After-fix WAV duration measured with real Piper
- [x] `tests/test_srt_voice.py` added and passing
- [x] Full test suite + compileall passing
- [x] Final PASS/FAIL recorded

### Step 1 — baseline (before fix), real Piper

- Command: `.venv\Scripts\python.exe _tmp_verify_1a.py before`
- Parsed text: `'Hey! Look at this.\n\nThis is so cool.\n\nI cannot believe it works.'`
- WAV duration: **5.364 s** (`_tmp_verify_1a_before.wav`)
- Result: baseline captured. PASS (as measurement step).

### Step 2 — `parse_srt_text` space-join

- Changed: `app/srt_voice.py::parse_srt_text`
  - `return "\n\n".join(texts)` -> `return " ".join(texts)`
  - Docstring updated to describe the one-flowing-line behavior.
- Result: implemented. PASS (verified live in Step 4).

### Step 3 — `generate_voice_wav` `--sentence-silence 0.5`

- Changed: `app/srt_voice.py`
  - New module constant `SENTENCE_SILENCE_SECONDS = 0.5`.
  - Piper command now ends with
    `"--sentence-silence", str(SENTENCE_SILENCE_SECONDS)`.
- Result: implemented. PASS (verified live in Step 4).

### Step 4 — after-fix timing, real Piper (NOT mocked)

- Command: `.venv\Scripts\python.exe _tmp_verify_1a.py after`
- Parsed text: `'Hey! Look at this. This is so cool. I cannot believe it works.'`
- WAV duration: **6.922 s** (`_tmp_verify_1a_after.wav`)
- Delta vs baseline: **+1.558 s**
- Expected silence: cue 1 contains two sentences ("Hey!" and
  "Look at this."), so there are 3 sentence boundaries x 0.5 s =
  1.5 s of inserted silence; the remaining ~0.06 s is normal
  Piper prosody variation between separate-line and in-line
  sentence synthesis.
- Result: PASS — inter-cue pauses are present in the generated WAV.

### Step 5 — deterministic tests (no Piper subprocess)

- Added `tests/test_srt_voice.py` (7 tests):
  - multi-cue SRT -> single-line, space-joined narration text (no `\n`)
  - multiline cue text joined with spaces; CRLF normalized; missing
    file raises `SrtError`
  - Piper command includes `--sentence-silence 0.5` (subprocess.run
    stubbed; asserts command list only)
  - temp input file written for Piper contains the space-joined text
    and is cleaned up; final WAV exists, `.part` removed
- Command: `.venv\Scripts\python.exe -m unittest tests.test_srt_voice -v`
- Result: 7/7 OK. PASS.

### Step 6 — full verification

- `.venv\Scripts\python.exe -m unittest discover -s tests -v`
  -> Ran 93 tests, OK (no existing test weakened or removed).
- `.venv\Scripts\python.exe -m compileall -q app tests` -> clean.
- Result: PASS.

### Final result — Bug 1A: PASS

- Before fix: 5.364 s (zero silence between cues).
- After fix: 6.922 s (+1.558 s = 3 sentence boundaries x 0.5 s +
  ~0.06 s prosody variation), measured with real Piper, not mocked.
- Inter-cue pauses are present in the generated WAV; exactly one WAV
  is still produced via the same Piper CLI subprocess with the same
  atomic write/cleanup behavior. No timeline/video changes.

### Remaining

- None. Bug 1A is closed. Bug 1B may now be started (approved fix
  direction recorded below), but was intentionally not started in this
  session.

---

## Bug 1B — Stop Voice does not cancel playback

Status: PASS

### Root cause (confirmed, for context)

- `play_wav_blocking` (`app/voice_preview.py`) calls
  `PlaySound(path, SND_FILENAME)`, which BLOCKS the worker thread.
  Probes (`_tmp_winsound_probe2.py`) confirmed `PlaySound(None, SND_PURGE)`
  from the UI thread cannot interrupt a blocking PlaySound running on
  another thread.
- Probe `_tmp_winsound_probe4.py` confirmed the fix direction: async
  playback (`SND_FILENAME | SND_ASYNC`) CAN be stopped cross-thread with
  `PlaySound(None, SND_PURGE)`.

### Approved fix direction (approved)

- Replace blocking playback with `PlaySound(path, SND_FILENAME | SND_ASYNC)`.
- Stop Voice keeps using `PlaySound(None, SND_PURGE)`.
- Remove playback worker threads; schedule completion via Tk `after()`
  using `probe_wav_duration`; token checks already invalidate stale
  callbacks after Stop. Clean up state on both preview and asset paths.

### Implementation plan (this session)

1. `app/voice_preview.py` — replace `play_wav_blocking` with
   `play_wav_async(wav_path)` calling
   `winsound.PlaySound(str(wav_path), SND_FILENAME | SND_ASYNC)`
   (returns immediately, stoppable cross-thread). `stop_sound()`
   (`PlaySound(None, SND_PURGE)`) stays unchanged.
2. `app/main.py` — import `play_wav_async` instead of
   `play_wav_blocking`; remove the two playback worker threads:
   - `_start_playback` (preview path): probe duration with
     `probe_wav_duration`, call `play_wav_async`, then schedule
     `self.after(duration_ms, lambda: self._on_playback_done(token, None))`.
     Probe/play failures route through `_on_playback_done(token, error)`.
   - `_on_play_asset` (asset path): same pattern with
     `_on_asset_playback_done`.
   - Delete `_playback_worker` and `_asset_playback_worker`.
   - `_on_playback_done` / `_on_asset_playback_done`: move the
     `_playing` / `_voice_playing` flag reset AFTER the token check so
     a stale completion callback can never corrupt a newer playback.
   - `_on_close`: call `stop_sound()` before destroying the window so
     app exit cleanly stops any async playback.
3. `tests/test_voice_playback.py` (new) — focused Bug 1B regression
   tests, no audio hardware, no real sound:
   - `play_wav_async` passes `SND_FILENAME | SND_ASYNC` to
     `winsound.PlaySound`; `stop_sound` passes `(None, SND_PURGE)`
     (winsound patched).
   - Real `App` with `app.main.play_wav_async` / `app.main.stop_sound`
     patched: Play starts async playback and enables Stop; Stop purges
     sound and resets UI state; natural completion (real `after()` +
     real `probe_wav_duration` on a tiny silent WAV) resets UI state;
     a stale completion callback after Stop+re-Play does not corrupt
     the newer playback (preview and asset paths); `_on_close` stops
     sound.
4. Verification: targeted `tests.test_voice_playback`, then full
   `unittest discover -s tests`, then `compileall -q app tests`.

### Files expected to change

- `app/voice_preview.py` (`play_wav_blocking` -> `play_wav_async`)
- `app/main.py` (playback start/stop/completion/close paths)
- `tests/test_voice_playback.py` (new)
- `docs/regression_part1_voice.md` (this file)

### Log

- [x] `play_wav_async` implemented in `app/voice_preview.py`
- [x] `app/main.py` switched to async playback + `after()` completion
- [x] Stale completion callbacks cannot corrupt newer playback
- [x] `_on_close` stops playback
- [x] `tests/test_voice_playback.py` added and passing (targeted)
- [x] Full test suite + compileall passing
- [x] Final PASS/FAIL recorded

### Step 1 — `play_wav_async` in `app/voice_preview.py`

- Changed: `app/voice_preview.py`
  - `play_wav_blocking` replaced by `play_wav_async`: calls
    `winsound.PlaySound(str(wav_path), SND_FILENAME | SND_ASYNC)` and
    returns immediately. Same `PreviewError` on non-Windows platforms.
  - `stop_sound()` unchanged: `PlaySound(None, SND_PURGE)`.
- Result: implemented.

### Step 2 — `app/main.py` async playback + `after()` completion

- Changed: `app/main.py`
  - Import `play_wav_async` instead of `play_wav_blocking`.
  - `_start_playback` (preview path): probes duration via
    `probe_wav_duration`, calls `play_wav_async`, schedules completion
    with `self.after(duration_ms, lambda: self._on_playback_done(token, None))`.
    Probe/play failures route through `_on_playback_done(token, error)`.
  - `_on_play_asset` (asset path): same pattern with
    `_on_asset_playback_done`.
  - Deleted `_playback_worker` and `_asset_playback_worker` (no more
    playback worker threads).
  - `_on_playback_done` / `_on_asset_playback_done`: flag reset
    (`_playing` / `_voice_playing`) moved AFTER the token check, so a
    stale completion callback cannot corrupt a newer playback.
  - `_on_close`: calls `stop_sound()` before teardown/destroy.
- Result: implemented.

### Step 3 — pending completion timers cancelled on Stop/close

- Found during testing: a pending completion `after()` callback fired
  after the window was destroyed (Tcl `invalid command name` error).
- Changed: `app/main.py`
  - New state: `_preview_after_id` / `_voice_after_id` hold the pending
    completion timer IDs.
  - New helper `_cancel_after(attr)` cancels and clears a pending timer.
  - `_start_playback` / `_on_play_asset` store the `after()` ID (and
    cancel any previous pending one first).
  - `_on_stop_preview`, `_cancel_preview_if_active`, `_on_stop_asset`
    and `_on_close` cancel the pending completion timer.
  - `_on_playback_done` / `_on_asset_playback_done` clear the ID only
    AFTER the token check (stale callbacks still touch no state).
- Result: implemented; no Tcl errors in targeted tests.

### Step 4 — focused regression tests

- Added `tests/test_voice_playback.py` (11 tests):
  - `play_wav_async` passes `SND_FILENAME | SND_ASYNC` to
    `winsound.PlaySound`; `stop_sound` passes `(None, SND_PURGE)`
    (winsound patched, no sound produced).
  - Real `App` with `app.main.play_wav_async` / `app.main.stop_sound`
    patched (no audio hardware):
    - asset path: Play starts async playback + enables Stop; Stop
      purges + resets UI; natural completion (real `after()` + real
      `probe_wav_duration` on a 0.03 s silent WAV) resets UI; stale
      completion after Stop+re-Play does not corrupt newer playback.
    - preview path: same four behaviors via `_start_playback` /
      `_on_stop_preview`.
    - `_on_close` stops sound while playback is active.
- Command: `.venv\Scripts\python.exe -m unittest tests.test_voice_playback -v`
- Result: 11/11 OK. PASS.

### Step 5 — full verification

- `.venv\Scripts\python.exe -m unittest discover -s tests -v`
  -> Ran 104 tests, OK (93 pre-existing + 11 new Bug 1B tests; no
  existing test weakened or removed).
- `.venv\Scripts\python.exe -m compileall -q app tests` -> clean.
- Result: PASS.

### Final result — Bug 1B: PASS

- Playback is now `PlaySound(path, SND_FILENAME | SND_ASYNC)` —
  non-blocking, no playback worker threads, and stoppable cross-thread
  (matches probe4's confirmed fix direction).
- Stop Voice / Stop (assets) call `PlaySound(None, SND_PURGE)` and
  cancel the pending completion timer, so playback really stops and UI
  state resets immediately.
- Natural completion is scheduled via Tk `after()` using the real
  probed WAV duration and resets UI state (verified with real timers
  on a 0.03 s silent WAV).
- Stale completion callbacks are discarded by the token check BEFORE
  any state mutation, so they cannot corrupt a newer playback
  (verified for both preview and asset paths).
- `_on_close` purges winsound playback and cancels pending completion
  timers before destroying the window.
- Deterministic regression coverage: `tests/test_voice_playback.py`
  (11 tests), no audio hardware, no real sound.

### Remaining

- None. Bug 1A and Bug 1B are both closed. Bug 2 / Phase 9 were
  intentionally not started in this session. No commit/push was made.
