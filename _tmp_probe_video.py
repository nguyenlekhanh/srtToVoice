"""Temporary Bug 2 probe: real VideoPlayer + fake sounddevice.

Generates a real 3 s MP4 (mpeg4 solid colors red/green/blue per second,
aac sine tones 440/880/1760 Hz per second, keyframe every 0.5 s) and
runs seek/play/pause/stop scenarios against the REAL VideoPlayer with
audio output replaced by a recording fake. No audio hardware used.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import av
import numpy as np
from PIL import Image

FIXTURE = Path("_tmp_fixture.mp4")
COLORS = [(200, 30, 30), (30, 200, 30), (30, 30, 200)]  # s0, s1, s2
FREQS = [440.0, 880.0, 1760.0]
FPS = 10
SR = 48000
DURATION = 3.0


def make_fixture() -> None:
    if FIXTURE.is_file():
        return
    container = av.open(str(FIXTURE), mode="w")
    vs = container.add_stream("mpeg4", rate=FPS)
    vs.width = 64
    vs.height = 64
    vs.pix_fmt = "yuv420p"
    vs.codec_context.gop_size = 5  # keyframe every 0.5 s
    aus = container.add_stream("aac", rate=SR)
    aus.layout = "stereo"

    for i in range(int(DURATION * FPS)):
        img = Image.new("RGB", (64, 64), COLORS[i // FPS])
        frame = av.VideoFrame.from_image(img)
        frame.pts = i
        for packet in vs.encode(frame):
            container.mux(packet)
    for packet in vs.encode():
        container.mux(packet)

    total = int(DURATION * SR)
    chunk = 1024
    for start in range(0, total, chunk):
        n = min(chunk, total - start)
        idx = np.arange(start, start + n)
        freq = np.array([FREQS[min(2, s // SR)] for s in idx])
        tone = (np.sin(2 * np.pi * freq * idx / SR) * 0.5 * 32767)
        tone = tone.astype(np.int16)
        interleaved = np.stack([tone, tone], axis=1).reshape(1, -1)
        aframe = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(interleaved), format="s16", layout="stereo"
        )
        aframe.sample_rate = SR
        aframe.pts = start
        for packet in aus.encode(aframe):
            container.mux(packet)
    for packet in aus.encode():
        container.mux(packet)
    container.close()


class FakeRawOutputStream:
    instances: list["FakeRawOutputStream"] = []

    def __init__(self, samplerate=None, channels=None, dtype=None,
                 blocksize=None, **_kw):
        self.samplerate = samplerate
        self.writes: list[tuple[float, bytes]] = []
        self.closed = False
        self.aborted = False
        FakeRawOutputStream.instances.append(self)

    def write(self, data):
        if self.closed:
            raise RuntimeError("stream closed")
        self.writes.append((time.monotonic(), bytes(data)))
        time.sleep(len(data) / (4 * self.samplerate))  # pace like device

    def stop(self):
        pass

    def start(self):
        pass

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


class FakeSd:
    RawOutputStream = FakeRawOutputStream


def dominant_freq(chunk: bytes, sr: int) -> float:
    samples = np.frombuffer(chunk, dtype=np.int16)[::2].astype(np.float64)
    if len(samples) < 64:
        return 0.0
    spec = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1 / sr)[np.argmax(spec[1:]) + 1])


def color_name(rgb) -> str:
    r, g, b = rgb
    if r > g and r > b:
        return "red"
    if g > r and g > b:
        return "green"
    return "blue"


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.frames: list[tuple[float, str]] = []
        self.ticks: list[tuple[float, float]] = []
        self.finished = threading.Event()
        self.errors: list[str] = []

    def on_frame(self, image):
        with self.lock:
            self.frames.append(
                (time.monotonic(), color_name(image.getpixel((32, 32))))
            )

    def on_tick(self, position, duration):
        with self.lock:
            self.ticks.append((time.monotonic(), position))

    def on_finished(self):
        self.finished.set()

    def on_error(self, message):
        self.errors.append(message)


def writes_after(t0: float) -> list[tuple[float, bytes]]:
    stream = FakeRawOutputStream.instances[-1]
    return [(t, d) for t, d in stream.writes if t >= t0]


def freqs_after(t0: float) -> list[float]:
    return [dominant_freq(d, SR) for _, d in writes_after(t0)]


def report(label: str, ok: bool, detail: str) -> None:
    print(f"[{'OK ' if ok else 'BAD'}] {label}: {detail}")


def main() -> None:
    make_fixture()
    import app.video_preview as vp
    vp.sd = FakeSd  # no audio hardware

    from app.video_preview import VideoPlayer

    rec = Recorder()
    player = VideoPlayer(
        FIXTURE, on_frame=rec.on_frame, on_tick=rec.on_tick,
        on_finished=rec.on_finished, on_error=rec.on_error,
    )
    print(f"fixture: duration={player.duration:.2f} "
          f"has_audio={player.info.has_audio}")

    # S1: plain play -------------------------------------------------
    player.play()
    time.sleep(1.2)
    pos = player.position
    freqs = freqs_after(0.0)
    with rec.lock:
        frame_colors = [c for _, c in rec.frames]
    report("S1 play", 0.8 <= pos <= 1.5 and freqs
           and all(abs(f - 440) < 40 for f in freqs[:3])
           and frame_colors and frame_colors[0] == "red",
           f"pos={pos:.2f} audio_freqs={[round(f) for f in freqs[:5]]} "
           f"frames={frame_colors[:6]}")

    # S2: seek while playing 0.5->2.0 --------------------------------
    seek_t = time.monotonic()
    player.seek(2.0)
    time.sleep(1.0)
    pos = player.position
    settle = seek_t + 0.45
    late_freqs = freqs_after(settle)
    with rec.lock:
        frames_after = [c for t, c in rec.frames if t >= seek_t]
    stale_audio = [f for f in late_freqs if abs(f - 1760) > 120]
    stale_video = [c for c in frames_after if c in ("red", "green")]
    report("S2 seek while playing",
           2.0 <= pos <= 3.0 and late_freqs and not stale_audio
           and frames_after and frames_after[-1] == "blue"
           and not stale_video,
           f"pos={pos:.2f} audio_freqs_after_settle="
           f"{[round(f) for f in late_freqs[:6]]} "
           f"frames_after_seek={frames_after[:8]} "
           f"stale_audio={len(stale_audio)} stale_video={stale_video[:4]}")

    # S3: stop while playing ------------------------------------------
    stop_t = time.monotonic()
    player.stop()
    time.sleep(0.4)
    n_writes_after_stop = len(writes_after(stop_t + 0.15))
    threads_alive = (
        (player._video_thread is not None and player._video_thread.is_alive())
        or (player._audio_thread is not None
            and player._audio_thread.is_alive())
    )
    report("S3 stop",
           player.position == 0.0 and not player.is_active
           and not threads_alive and n_writes_after_stop == 0,
           f"pos={player.position:.2f} active={player.is_active} "
           f"threads_alive={threads_alive} "
           f"writes_after_stop={n_writes_after_stop}")

    # S4: seek while stopped, then play --------------------------------
    player.seek(1.5)
    play_t = time.monotonic()
    player.play()
    time.sleep(0.9)
    pos = player.position
    freqs = freqs_after(play_t + 0.15)
    with rec.lock:
        frames_after = [c for t, c in rec.frames if t >= play_t]
    report("S4 seek-while-stopped then play",
           1.5 <= pos <= 2.6 and freqs
           and all(abs(f - 880) < 60 for f in freqs[:4])
           and frames_after and "red" not in frames_after[:6],
           f"pos={pos:.2f} audio_freqs={[round(f) for f in freqs[:6]]} "
           f"frames={frames_after[:8]}")

    # S5: replay after natural end -------------------------------------
    player.stop()
    player.seek(2.6)
    player.play()
    rec.finished.clear()
    rec.finished.wait(timeout=3.0)
    time.sleep(0.2)
    replay_t = time.monotonic()
    player.play()  # play again after finished
    time.sleep(0.6)
    pos = player.position
    report("S5 replay after end",
           player.is_active and 0.2 <= pos <= 1.2,
           f"pos_after_0.6s={pos:.2f} active={player.is_active} "
           "(expect ~0.6, not ~3.0 fast-forward)")

    # S6: pause / resume ------------------------------------------------
    pause_t = time.monotonic()
    player.pause()
    time.sleep(0.35)
    n_at_pause = len(writes_after(pause_t + 0.25))
    time.sleep(0.3)
    n_later = len(writes_after(pause_t + 0.25))
    paused_ok = player.is_playing is False and n_later == n_at_pause
    player.play()  # resume
    time.sleep(0.5)
    resumed_ok = player.is_playing and len(writes_after(pause_t + 0.6)) > 0
    report("S6 pause/resume", paused_ok and resumed_ok,
           f"paused_writes_delta={n_later - n_at_pause} "
           f"is_playing_paused={player.is_playing} resumed={resumed_ok}")

    player.stop()
    print("errors:", rec.errors)


if __name__ == "__main__":
    main()

