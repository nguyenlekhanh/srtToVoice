# Part 2 Regression — Video Timeline Playback / Audio (Bug 2)

Source of truth for the Part 2 debugging session. Another model can open
a new chat and resume exactly where this work stopped by reading this file.

Scope: video preview + timeline playback reliability. Voice behavior
(Part 1: Bug 1A/1B) must NOT change. Timeline editing model behavior
(Phase 5–8: move/select/clamp/trim) must NOT change.

Rules:
- Do not weaken or remove existing tests.
- Investigate first; do not assume FFmpeg is required; no architecture
  rewrite without evidence.
- If a proposed fix fails, record the evidence here and STOP.
- Do not commit or push.

Status: IN PROGRESS — Step 4 complete (RC5 + RC6 fixed); Step 5 pending

---

## Current state (at session start)

- Git: branch `main`, working tree clean, HEAD `fc48e21`
  (Bug 1A + Bug 1B committed and PASS per
  `docs/regression_part1_voice.md`).
- Full suite before Bug 2 work: 104/104 OK.
- Playback architecture (read, not yet modified):
  - `app/video_preview.py::VideoPlayer` — PyAV decode on two daemon
    threads (video frames → `on_frame` PIL images; audio → resampled
    s16 stereo 48 kHz → `sounddevice.RawOutputStream`). A/V sync:
    video paces against `_audio_clock` (wall clock when no audio);
    audio waits while `audio_clock - video_clock >= _MAX_AV_DRIFT`
    (0.25 s). No system FFmpeg, no subprocess (PyAV bundles FFmpeg
    libs). Environment confirmed: av 18.1.0, numpy 2.5.2,
    sounddevice 0.5.6, PIL 12.3.0; `mpeg4` + `aac` encoders available
    in the wheel (can build real test fixtures without system FFmpeg).
  - Seek: ONE shared `_seek_to` slot; `seek()` sets it when playing;
    BOTH `_run_video_worker` and `_run_audio_worker` call
    `_consume_seek()` (first caller wins, other gets None).
  - `app/main.py`: `_load_video` probes + creates VideoPlayer +
    `timeline.set_video(path, duration)`; timeline click and seek
    slider call `player.seek(target)` and set `_video_seek_pending`
    for 150 ms via untracked `after()` callbacks; during that window
    `_apply_video_tick` only suppresses the slider var, NOT the time
    label / playhead.

## Bug 2 symptoms (user report)

1. Opening a video must put it on the timeline (verify it does).
2. Start / Pause / Stop playback must behave deterministically.
3. Clicking/seeking on the timeline must not make playback jump or
   behave erratically.
4. Video playback must produce audible video audio when the source
   video contains an audio track.

## Root-cause investigation plan

Evidence before fixes, using a temporary probe script
(`_tmp_probe_video.py`, deleted at the end):

1. Generate a real tiny MP4 fixture with PyAV itself (mpeg4 video with
   per-second solid colors red/green/blue + aac stereo sine tones
   440/880/1760 Hz per second; keyframes every ~0.5 s). Color + tone
   frequency identify WHICH second of content is being shown/heard.
2. Patch `app.video_preview.sd` with a fake sounddevice module whose
   `RawOutputStream` records writes and paces them in real time (like a
   real device), so the real VideoPlayer threads run with real decoding
   but no audio hardware.
3. Scenarios:
   - S1 plain play: audio bytes flow, correct tone, position advances.
   - S2 seek while playing (0.5 → 2.0): do BOTH workers re-seek?
     Content after seek must be blue/1760 Hz, no stale red/440 burst.
   - S3 stop while playing: position 0, threads exit, writes stop.
   - S4 seek while stopped then play: must start at target, not snap
     to 0, audio must not stall.
   - S5 play again after natural end: must restart at real-time pace,
     not fast-forward.
   - S6 pause/resume: writes stop promptly on pause (stream flushed),

### Suspected root causes (from code reading — to confirm)

- RC1 seek race: single `_seek_to` slot consumed by whichever worker
  calls `_consume_seek()` first; the other worker never re-seeks its
  container → stale video replay / stalled or stale audio after any
  timeline click. [symptoms 2, 3, 4]
- RC2 play ignores start position: workers open the container at t=0;
  seek-while-stopped only sets clocks → Play snaps position back to 0
  and audio sync-wait stalls (silence). [symptoms 2, 3, 4]
- RC3 replay after natural end: `_audio_ready`/`_audio_clock` left
  stale (~duration) → replay races to the end. [symptom 2]
- RC4 post-seek stale burst: frames between the keyframe and the seek
  target are shown/written (flash of pre-target content). [symptom 3]
- RC5 pause/stop tail: pause does not flush the PortAudio stream
  (buffered tail keeps sounding); close uses `stop()` (plays out tail)
  not `abort()`. [symptom 2]
- RC6 UI stale ticks: during the 150 ms `_video_seek_pending` window,
  pre-seek ticks still move the time label + timeline playhead
  (playhead jumps back then forward after a click); multiple
  untracked `after(150, _clear_seek_pending)` timers. [symptom 3]

## Exact implementation steps (planned)

1. Create this progress file (IN PROGRESS).
2. Run probe scenarios S1–S6; record evidence; confirm/reject RC1–RC6.
3. Fix confirmed causes in `app/video_preview.py` only:
   - per-worker seek slots (`_video_seek_to` / `_audio_seek_to`) so
     BOTH workers always re-seek; inner audio frame loop aborts to the
     seek check promptly.
   - `play()` fresh start: restart from 0 when position >= duration;
     re-anchor clocks (`_audio_clock = _position`, `_audio_ready =
     False`); enqueue initial seeks to `_position` so playback starts
     where the user seeked while stopped.
   - drop pre-target frames after a seek (decode but don't show/write
     content before the seek target).
   - pause flushes the audio stream (discard buffered tail); close
     aborts instead of stopping-plays-out.
4. Fix confirmed UI causes in `app/main.py`:
   - `_apply_video_tick`: ignore ticks entirely while
     `_video_seek_pending`.
   - single tracked `_seek_after_id` (cancel previous before
     rescheduling) in `_on_seek_drag` + `_on_timeline_click`; cancel
     on teardown/close.
5. Add `tests/test_video_playback.py` regression tests (real PyAV
   fixture + fake sounddevice; no audio hardware): one test per
   confirmed bug + symptom-1 guard (video lands on timeline).
6. Targeted tests, then full suite, then `compileall`.
7. Mark PASS, record exact results, final `git diff`/`status` review.

## Files expected to change

- `app/video_preview.py` (VideoPlayer seek/play/pause/stop internals)
- `app/main.py` (tick suppression + seek-timer tracking only)
- `tests/test_video_playback.py` (new)
- `docs/regression_part2_video_timeline.md` (this file)

## Log

### Step 2 — probe evidence (before fix)

Probe: `_tmp_probe_video.py` (real PyAV fixture 3 s, mpeg4 red/green/blue
per second + aac 440/880/1760 Hz per second, keyframe 0.5 s; real
VideoPlayer threads; `app.video_preview.sd` replaced by a recording fake
`RawOutputStream` that paces writes in real time — no audio hardware).

```
fixture: duration=3.00 has_audio=True
[OK ] S1 play: pos=1.10 audio_freqs=[422,422,422,422,422] frames=['red'x6]
[BAD] S2 seek while playing: pos=3.00 audio_freqs_after_settle=[]
      frames_after_seek=['green'x6] stale_video=['green'x4]
[OK ] S3 stop: pos=0.00 active=False threads_alive=False writes_after_stop=0
[BAD] S4 seek-while-stopped then play: pos=3.00 audio_freqs=[422x6]
      frames=['red'x8]
[BAD] S5 replay after end: pos_after_0.6s=3.00 active=False
      (expected ~0.6, got instant fast-forward to end)
[BAD] S6 pause/resume: resumed=False  (contaminated by S5 end-state;
      re-probe in isolation after fixes)
errors: []
```

Confirmed root causes:
- RC1 CONFIRMED (S2): single `_seek_to` slot is consumed by whichever
  worker calls `_consume_seek()` first; the other worker never re-seeks
  its container. After seek 0.5→2.0 the video shows stale 1 s content
  (green), audio goes silent (no writes after settle), and position
  races to the end (3.00).
- RC2 CONFIRMED (S4): `play()` always opens the container at t=0 and
  ignores the position set by a seek-while-stopped. After seek(1.5)+play
  the video replays red from 0, audio writes 440 Hz (0 s content) while
  the audio clock was pre-set to 1.5 → clock/position mismatch makes
  video race to the end (pos=3.00 in 0.9 s).
- RC3 CONFIRMED (S5): after natural end, `_audio_ready`/`_audio_clock`
  are left stale, so a second `play()` fast-forwards to the end
  instantly instead of restarting at real-time pace.
- RC4 CONFIRMED (S2/S4): pre-target frames are shown after a seek
  (green/red flash) — content before the seek target is decoded and
  emitted instead of dropped.
- RC5 (pause/stop audio tail) and RC6 (UI stale ticks): not yet
  evidenced in isolation; S6 was contaminated by S5. Re-probe after
  RC1–RC4 fixes.

### Step 3 — implement confirmed causes RC1–RC4 (app/video_preview.py only)

Status: RC1–RC4 implemented and probe-verified. RC5/RC6 NOT started.

#### 3a. RC1 — independent per-worker seeks (implemented, VERIFIED)

- `VideoPlayer.__init__`: replaced the single shared `_seek_to` slot with
  two independent slots `_video_seek_to` / `_audio_seek_to`.
- `VideoPlayer.seek()`: when playing, sets BOTH slots so each worker
  re-seeks its own container.
- `VideoPlayer._consume_seek(attr)`: now pops only the calling worker's
  own slot.
- `_run_video_worker` consumes `_video_seek_to`; `_run_audio_worker`
  consumes `_audio_seek_to`.
- Prompt notice: the audio inner frame loop and its A/V sync-wait both
  check `_audio_seek_to is not None` and break out to the outer loop,
  so a new seek is handled within ~5 ms instead of after the current
  audio frame finishes playing.

#### 3b. RC2 — play from a stopped seek position (implemented, VERIFIED)

- `VideoPlayer.play()` fresh-start branch:
  - restarts from 0 ONLY when `_position >= duration` (natural end);
  - re-anchors clocks: `_audio_clock = _position`, `_audio_ready = False`,
    `_video_start_wall/_video_start_pts = None`;
  - enqueues initial seeks `_video_seek_to = _audio_seek_to = _position`
    when `_position > 0`, so both workers open+seek their containers to
    the user's seek-while-stopped position instead of t=0.
- `VideoPlayer.seek()` while stopped also resets `_audio_ready = False`
  and the wall-clock anchors.

#### 3c. RC3 — replay after natural end (implemented, VERIFIED)

- Covered by the `play()` fresh-start branch above: position reset to 0
  at end + `_audio_clock = 0.0` + `_audio_ready = False` remove the
  stale clock that made replay fast-forward to the end.

#### 3d. RC4 — discard pre-target content after seek (implemented, VERIFIED)

- Both workers keep a local `drop_until` set to the seek target after
  re-seeking:
  - video: frames with `frame_time < drop_until` are decoded but skipped
    (not shown, `_position` not updated);
  - audio: frames whose end time (`pts*time_base + samples/sample_rate`)
    is `<= drop_until` are dropped (not written, `_audio_clock` not
    advanced).
- In-flight frame guard (added this session): if a seek arrives while a
  pre-seek frame is blocked in the sync wait, BOTH workers re-check the
  pending-seek slot right after the wait and drop that frame instead of
  emitting it (video: skip without updating `_position`; audio: break to
  outer loop without writing/advancing the clock). Without this guard
  the probe showed exactly one stale pre-seek frame after S2 seek.
- Seek offset unit fix (both workers): with `stream=` the offset must be
  in STREAM time_base units — `int(target / float(stream.time_base))`,
  not `int(target * av.time_base)` (microseconds), which seeked ~1000x
  past EOF.

#### 3e. Blocking defect found during verification (fixed, in scope of RC1/RC2/RC4)

- Symptom: after the 3a–3d implementation, S2/S4 still failed — both
  workers re-seeked correctly, but the VIDEO stream produced only ~3
  frames after `container.seek()` and then reported end-of-stream,
  killing the video worker (position snapped to duration, audio worker
  then quit too).
- Evidence (standalone diag via `_tmp_debug_s4.py`):
  - seek math verified correct: video tb=1/10240, audio tb=1/48000;
    target 1.5 → video offset 15360 lands on pts 1.5, audio offset
    72000 lands on pts 1.4933 with 71 frames remaining;
  - `thread_type="AUTO"` (frame threading) after seek: 3 frames then EOF;
  - `thread_type="NONE"` after seek: all 15 frames decode;
  - audio stream with AUTO is unaffected (71/71 frames).
- Fix: `_run_video_worker` sets `stream.thread_type = "NONE"` on the
  video stream (frame-threaded decode mis-reports EOF after a flush/seek
  in this PyAV/FFmpeg build; single-threaded preview decode is fine).
  Audio keeps `"AUTO"`.

#### 3f. Probe results after RC1–RC4 fixes (exact output, stable over 3 runs)

Command: `.venv\Scripts\python.exe _tmp_probe_video.py`

```
fixture: duration=3.00 has_audio=True
[OK ] S1 play: pos=1.10 audio_freqs=[422, 422, 422, 422, 422] frames=['red', 'red', 'red', 'red', 'red', 'red']
[OK ] S2 seek while playing: pos=3.00 audio_freqs_after_settle=[1781, 1781, 1781, 1781, 1781, 1781] frames_after_seek=['blue', 'blue', 'blue', 'blue', 'blue', 'blue', 'blue', 'blue'] stale_audio=0 stale_video=[]
[OK ] S3 stop: pos=0.00 active=False threads_alive=False writes_after_stop=0
[OK ] S4 seek-while-stopped then play: pos=2.30 audio_freqs=[891, 891, 891, 891, 891, 891] frames=['green', 'green', 'green', 'green', 'green', 'blue', 'blue', 'blue']
[OK ] S5 replay after end: pos_after_0.6s=0.50 active=True (expect ~0.6, not ~3.0 fast-forward)
[OK ] S6 pause/resume: paused_writes_delta=0 is_playing_paused=True resumed=True
errors: []
```

Interpretation:
- S1 OK: plain play unchanged (440 Hz tone measured as 422 Hz due to
  FFT bin resolution on 1024-sample chunks; red frames; real-time pace).
- S2 OK: seek 0.5→2.0 while playing — BOTH workers re-seek; after the
  seek only blue frames (2 s content) and only 1760 Hz audio (measured
  1781); zero stale audio/video; pos=3.00 is the NATURAL end (seek at
  ~1.1 s + 1.0 s sleep reaches 2.1+ s, then plays out to 3.0).
- S3 OK: stop resets to 0, threads exit, no writes after stop.
- S4 OK: seek(1.5) while stopped + play starts at 1.5 — green frames
  (1 s content) then blue, 880 Hz audio (measured 891), pos=2.30 after
  0.9 s (1.5 + ~0.8 real-time), no red, no stall.
- S5 OK: replay after natural end runs at real-time pace (pos≈0.5 after
  0.6 s, still active) — no fast-forward.
- S6 OK in isolation (was contaminated by S5 before): pause stops writes
  immediately (delta 0), resume continues. NOTE: S6 passing is evidence
  for RC5's pause-flush part (the `pause()` → `_flush_audio_stream()`
  call already present in the working tree), but RC5 is NOT formally
  closed: `close()`/`_close_audio_stream()` still use `stop()` (plays
  out buffered tail) instead of `abort()` — re-probe/decide in Step 4.

Regression checks:
- `python -m unittest discover -s tests -q` → `Ran 104 tests ... OK`
  (104/104, unchanged).
- `python -m compileall -q app tests` → clean.

#### Remaining work (resume here)

1. RC5 — DONE in Step 4 (see below).
2. RC6 — DONE in Step 4 (see below).
3. Step 5: add `tests/test_video_playback.py` regression tests (real
   PyAV fixture + fake sounddevice) for each confirmed bug + symptom-1
   guard.
4. Step 6/7: full suite + compileall again, mark PASS, final diff
   review. Delete `_tmp_probe_video.py`, `_tmp_debug_s4.py`,
   `_tmp_probe_rc5.py`, `_tmp_probe_rc6.py`, `_tmp_fixture.mp4` at the
   very end.
5. Do not commit or push. Do not start Bug 3.

### Step 4 — RC5 + RC6 (this session)

#### RC5: audio tail on pause/close — CONFIRMED + FIXED

Evidence (PortAudio docs, authoritative):
- `Pa_StopStream`: "Terminates audio processing. It waits until all
  pending audio buffers have been played before it returns." → DRAINS
  the buffered tail.
- `Pa_AbortStream`: terminates immediately, discarding pending buffers.

Reproduction (`_tmp_probe_video.py`-style probe `_tmp_probe_rc5.py`,
fake device that models the in-flight residual buffer; real
VideoPlayer threads, real decoding):

```
write_sizes(sample)=[4096, 4096, 4096] n_writes=66
[BAD] RC5 close tail: 2048 bytes (~11 ms) played after close() aborted=False
[BAD] RC5 pause tail: 2048 bytes (~11 ms) played after pause() aborted=False
RC5 CONFIRMED: buffered audio continues after pause/close (stop() drains the device buffer; abort() discards it).
```

Fix (minimal, `app/video_preview.py` only):
- `_close_audio_stream()`: `stream.stop()` → `stream.abort()` before
  `close()` (no drain on stop/close/teardown).
- `_flush_audio_stream()`: `stream.stop()` → `stream.abort()` before
  `start()` (no drain on pause/seek flush either).

Verification after fix:

```
write_sizes(sample)=[4096, 4096, 4096] n_writes=63
[OK ] RC5 close tail: 0 bytes (~0 ms) played after close() aborted=True
[OK ] RC5 pause tail: 0 bytes (~0 ms) played after pause() aborted=True
```

#### RC6: stale UI ticks during seek-pending window — CONFIRMED + FIXED

Reproduction (`_tmp_probe_rc6.py`: real `App` + fake player, exactly
the `tests/test_timeline_ui.py` harness style; `after()`/`after_cancel`
captured deterministically):

```
[BAD] RC6 stale tick during seek-pending: playhead=1.05 label='00:01 / 00:10' (expected to stay at ~5.00 / '00:05 ...')
[BAD] RC6 untracked timers: pending_clear_timers=2 playhead=1.10 (expected 1 timer and playhead ~7.00)
[OK ] RC6 ticks resume after window: pending=False playhead=7.20
```

Confirmed defects:
1. `_apply_video_tick` only suppressed `seek_var`; the time label and
   timeline playhead still snapped back to the pre-seek position
   (visible jump back then forward).
2. Both `_on_seek_drag` and `_on_timeline_click` scheduled UNTRACKED
   `after(150, _clear_seek_pending)` timers; a rapid second seek left
   two timers and the first one cleared the pending flag early.

Fix (minimal, `app/main.py` only):
- `_apply_video_tick`: while `_video_seek_pending`, ignore the tick
  entirely (label + slider + playhead all frozen at the seek target).
- New tracked `self._seek_after_id` (init `None`); both seek paths
  cancel the previous timer via `_cancel_after("_seek_after_id")`
  before scheduling the new 150 ms one.
- `_teardown_video_player`: cancels `_seek_after_id` and resets
  `_video_seek_pending` (clean teardown/close).

Verification after fix:

```
[OK ] RC6 stale tick during seek-pending: playhead=5.00 label='00:05 / 00:10' (expected to stay at ~5.00 / '00:05 ...')
[OK ] RC6 untracked timers: pending_clear_timers=1 playhead=7.00 (expected 1 timer and playhead ~7.00)
[OK ] RC6 ticks resume after window: pending=False playhead=7.20
```

#### Regression checks after RC5 + RC6

- `_tmp_probe_video.py` (S1–S6): all OK, e.g.
  `S1 pos=1.10`, `S2 stale_audio=0 stale_video=[]`, `S3 writes_after_stop=0`,
  `S4 pos=2.30`, `S5 pos_after_0.6s=0.50`, `S6 paused_writes_delta=0`,
  `errors: []`.
- `python -m unittest discover -s tests -q` → `Ran 104 tests in 7.098s`
  `OK` (104/104, unchanged).
- `python -m compileall -q app tests` → clean (exit 0).

#### Remaining work (resume here)

1. Step 5: add `tests/test_video_playback.py` regression tests (real
   PyAV fixture + fake sounddevice) for each confirmed bug + symptom-1
   guard.
2. Step 6/7: full suite + compileall again, mark PASS, final diff
   review. Delete `_tmp_probe_video.py`, `_tmp_debug_s4.py`,
   `_tmp_probe_rc5.py`, `_tmp_probe_rc6.py`, `_tmp_fixture.mp4` at the
   very end.
3. Do not commit or push. Do not start Bug 3.

Scratch files currently present (untracked, delete at the end):
`_tmp_probe_video.py` (the authoritative S1–S6 probe), `_tmp_debug_s4.py`
(repurposed diag script), `_tmp_probe_rc5.py` (RC5 tail probe),
`_tmp_probe_rc6.py` (RC6 stale-tick probe), `_tmp_fixture.mp4`
(generated fixture).
