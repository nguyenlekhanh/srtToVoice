"""Empirical probe: does PlaySound(None, ...) stop a BLOCKING PlaySound
running on ANOTHER thread? This mimics the app exactly:
  worker thread: PlaySound(path, SND_FILENAME)   # synchronous, blocks
  main thread:   PlaySound(None, SND_PURGE)      # stop request
Detection: measure whether the worker's blocking call returns early.
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

print("=== blocking-playback stop probe (mimics app) ===")

result = {}


def worker():
    t0 = time.monotonic()
    try:
        # Exactly what play_wav_blocking does: synchronous, no SND_ASYNC.
        winsound.PlaySound(main_path, winsound.SND_FILENAME)
        result["error"] = None
    except Exception as e:
        result["error"] = repr(e)
    result["elapsed"] = time.monotonic() - t0


t = threading.Thread(target=worker, daemon=True)
t.start()
time.sleep(1.0)  # let blocking playback get going
print("Calling PlaySound(None, SND_PURGE) from main thread at t=1.0s ...")
winsound.PlaySound(None, winsound.SND_PURGE)
t.join(timeout=6.0)

elapsed = result.get("elapsed")
err = result.get("error")
print("worker returned after %.2fs (wav is 4.0s), error=%r" % (elapsed, err))
if elapsed is not None and elapsed < 3.0:
    print("RESULT: STOP WORKED (blocking call returned early)")
else:
    print("RESULT: STOP DID NOT WORK (blocking call ran to ~full duration)")
print("=== done ===")
