"""Empirical probe: does winsound.PlaySound(None, ...) stop async playback?

Uses SILENT WAVs so nothing is audible. Detection: PlaySound with SND_NOSTOP
returns FALSE (winsound raises) if a sound is already playing.
"""
import os
import tempfile
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
make_wav(main_path, 3.0)
make_wav(probe_path, 0.2)


def probe_playing():
    """Non-destructive check: True if a PlaySound sound is currently playing."""
    try:
        winsound.PlaySound(
            probe_path,
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NOSTOP,
        )
    except Exception:
        return True
    time.sleep(0.35)  # let the short silent probe finish naturally
    return False


print("=== winsound stop-behavior probe ===")
print("SND_PURGE =", winsound.SND_PURGE, "SND_NOSTOP =", winsound.SND_NOSTOP)

# Sanity A: NOSTOP should fail (raise) right after starting a sound.
winsound.PlaySound(main_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
time.sleep(0.3)
try:
    winsound.PlaySound(
        probe_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NOSTOP
    )
    print("Sanity A: NOSTOP did NOT raise while playing -> detection BROKEN")
    time.sleep(0.35)
except Exception as e:
    print("Sanity A: NOSTOP raised while playing -> detection OK (%s)" % type(e).__name__)
time.sleep(3.5)  # let main finish naturally

# Sanity B: after natural finish, nothing should be playing.
print("Sanity B (finished): playing =", probe_playing(), " (expect False)")

# Test 1: stop with SND_PURGE (what the app currently does).
winsound.PlaySound(main_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
time.sleep(0.5)
winsound.PlaySound(None, winsound.SND_PURGE)
time.sleep(0.3)
print("Test 1 PlaySound(None, SND_PURGE): still playing =", probe_playing(), " (want False)")
time.sleep(3.5)

# Test 2: stop with flags=0.
winsound.PlaySound(main_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
time.sleep(0.5)
winsound.PlaySound(None, 0)
time.sleep(0.3)
print("Test 2 PlaySound(None, 0)        : still playing =", probe_playing(), " (want False)")
time.sleep(3.5)

print("=== done ===")
