"""Diag: replicate S2 (seek while playing) with state dumps."""
from __future__ import annotations
import time
import _tmp_probe_video as probe

probe.make_fixture()
import app.video_preview as vp
vp.sd = probe.FakeSd

rec = probe.Recorder()
player = vp.VideoPlayer(
    probe.FIXTURE, on_frame=rec.on_frame, on_tick=rec.on_tick,
    on_finished=rec.on_finished, on_error=rec.on_error,
)
player.play()
time.sleep(1.2)
print(f"before seek: pos={player.position:.2f}")
seek_t = time.monotonic()
player.seek(2.0)
for i in range(8):
    time.sleep(0.15)
    with player._lock:
        print(
            f"t={i*0.15:.2f} pos={player._position:.2f} "
            f"aclock={player._audio_clock:.2f} aready={player._audio_ready} "
            f"vseek={player._video_seek_to} aseek={player._audio_seek_to} "
            f"playing={player._playing} "
            f"valive={player._video_thread.is_alive() if player._video_thread else None} "
            f"aalive={player._audio_thread.is_alive() if player._audio_thread else None}"
        )
with rec.lock:
    after = [(round(t - seek_t, 2), c) for t, c in rec.frames if t >= seek_t]
print("frames after seek:", after)
stream = probe.FakeRawOutputStream.instances[-1]
late = [(round(t - seek_t, 2), len(d)) for t, d in stream.writes
        if t >= seek_t]
print("writes after seek:", late[:12], "total", len(late))
print("finished set:", rec.finished.is_set(), "errors:", rec.errors)
player.stop()
