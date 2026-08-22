"""Probe the FIX for Bug 1B: async playback + cross-thread stop + completion.

Scenario mimics the proposed fix:
  worker thread: PlaySound(path, SND_FILENAME | SND_ASYNC)  -> returns fast
                 then poll until sound is no longer playing (or stop event)
  main thread:   PlaySound(None, ...) to stop early

We test:
  T1: does PlaySound(None, SND_PURGE) from main thread stop ASYNC playback?
  T2: reliable side-effect-free 'still playing' detection for the poll loop.
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
probe_path = os.path.join(tmpdir, "probe.wav")
make_wav(main_path, 4.0)
make_wav(probe_path, 0.05)


def is_playing_nostop():
    """Detect current PlaySound playback. Returns True if something is playing.

    Uses SND_NOSTOP which raises RuntimeError if a sound is already playing
    (and does NOT start our probe, because NOSTOP refuses to preempt).
    When nothing is playing, it WOULD start the probe sound, so we immediately
    stop it with PlaySound(None). Side effect is a <=50ms silent blip.
    """
    try:
        winsound.PlaySound(
            probe_path,
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NOSTOP,
        )
    except RuntimeError:
        return True  # something already playing -> NOSTOP refused
    # Nothing was playing; the tiny probe just started. Stop it right away.
    winsound.PlaySound(None, winsound.SND_PURGE)
    return False


print("=== T1: async playback + cross-thread stop ===")
winsound.PlaySound(main_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
time.sleep(0.8)
print("  is_playing before stop:", is_playing_nostop(), "(expect True)")
winsound.PlaySound(None, winsound.SND_PURGE)
time.sleep(0.2)
print("  is_playing after stop :", is_playing_nostop(), "(expect False)")
time.sleep(4.5)  # ensure nothing resumes

print("=== T2: natural completion detection ===")
winsound.PlaySound(main_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
time.sleep(0.3)
print("  is_playing at 0.3s:", is_playing_nostop(), "(expect True)")
time.sleep(4.2)  # let the 4s sound finish
print("  is_playing at 4.5s:", is_playing_nostop(), "(expect False)")

print("=== done ===")
