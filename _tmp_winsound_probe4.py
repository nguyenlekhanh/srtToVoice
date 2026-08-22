"""Probe the EXACT app fix scenario for Bug 1B:
  worker thread: PlaySound(path, SND_FILENAME | SND_ASYNC)  (returns fast)
  main/UI thread: PlaySound(None, SND_PURGE)  -> must stop the async sound
This tests whether a cross-thread stop works for ASYNC playback.
"""
import os
import tempfile
import threading
import time
import wave
import winsound

RATE = 22050


def make_wav(path, dur):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x00\x00" * int(dur * RATE))


tmpdir = tempfile.mkdtemp()
main_path = os.path.join(tmpdir, "main.wav")
make_wav(main_path, 4.0)

print("=== cross-thread ASYNC stop probe (worker plays, UI stops) ===")
started = threading.Event()


def worker():
    # Exactly the proposed fix: async playback on the worker thread.
    winsound.PlaySound(main_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    started.set()


t = threading.Thread(target=worker, daemon=True)
t.start()
started.wait(timeout=2.0)
time.sleep(1.0)  # let async playback get going
print("  [t=1.0s] UI thread calling PlaySound(None, SND_PURGE) ...")
winsound.PlaySound(None, winsound.SND_PURGE)
time.sleep(4.0)  # wait past the 4s mark
# If stop FAILED, the sound would still be playing here (we cannot hear it,
# so instead detect via SND_NOSTOP probe).
probe_path = os.path.join(tmpdir, "probe.wav")
make_wav(probe_path, 0.05)
try:
    winsound.PlaySound(
        probe_path,
        winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NOSTOP,
    )
    still_playing = False  # NOSTOP succeeded -> nothing was playing
    winsound.PlaySound(None, winsound.SND_PURGE)  # stop the tiny probe
except RuntimeError:
    still_playing = True  # NOSTOP refused -> main sound still playing

print("  sound still playing 4s after stop call:", still_playing)
if not still_playing:
    print("RESULT: CROSS-THREAD ASYNC STOP WORKED")
else:
    print("RESULT: CROSS-THREAD ASYNC STOP FAILED")
print("=== done ===")
