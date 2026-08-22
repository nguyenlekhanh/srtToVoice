"""Temporary Bug 2 RC5 probe: audio tail on pause/close (stop vs abort).

Models the PortAudio output buffer: the device always holds back a small
residual of not-yet-played audio. Per the PortAudio docs,
``Pa_StopStream`` (= ``stream.stop()``) "waits until all pending audio
buffers have been played" (it DRAINS the residual), while
``Pa_AbortStream`` (= ``stream.abort()``) discards it immediately. This
probe measures whether audio bytes are still played AFTER the user
paused / closed, i.e. the audible tail.
"""
from __future__ import annotations

import time

from _tmp_probe_video import FIXTURE, make_fixture


class TailStream:
    instances: list["TailStream"] = []

    def __init__(self, samplerate=None, channels=None, dtype=None,
                 blocksize=None, **_kw):
        self.samplerate = samplerate or 48000
        self.pending = b""     # queued in the device, not yet played
        self.drained = b""     # bytes stop() played out AFTER the call
        self.aborted = False
        self.closed = False
        self.write_sizes: list[int] = []
        TailStream.instances.append(self)

    def write(self, data):
        data = bytes(data)
        self.write_sizes.append(len(data))
        bps = 4 * self.samplerate  # stereo s16 bytes/second
        # Device consumes everything except a trailing residual that is
        # still "in flight" (the not-yet-heard buffer).
        hold = min(len(data) // 2, 8000)
        consume = data[: len(data) - hold]
        self.pending = data[len(data) - hold:]
        if consume:
            time.sleep(len(consume) / bps)

    def stop(self):
        # Pa_StopStream: wait until all pending buffers have been played.
        if self.pending:
            self.drained += self.pending
            time.sleep(len(self.pending) / (4 * self.samplerate))
            self.pending = b""

    def abort(self):
        # Pa_AbortStream: discard pending buffers immediately.
        self.aborted = True
        self.pending = b""

    def start(self):
        pass

    def close(self):
        self.closed = True


class FakeSd:
    RawOutputStream = TailStream


def tail_ms(n_bytes: int, sr: int = 48000) -> float:
    return n_bytes / (4 * sr) * 1000.0


def main() -> None:
    make_fixture()
    import app.video_preview as vp
    vp.sd = FakeSd  # no audio hardware
    from app.video_preview import VideoPlayer

    def mk() -> VideoPlayer:
        return VideoPlayer(
            FIXTURE, on_frame=lambda i: None, on_tick=lambda a, b: None,
            on_finished=lambda: None, on_error=lambda m: None,
        )

    # Scenario A: audio tail on CLOSE/STOP ----------------------------
    p = mk()
    p.play()
    time.sleep(0.8)
    p.close()
    time.sleep(0.1)
    s_close = TailStream.instances[-1]
    close_tail = len(s_close.drained)

    # Scenario B: audio tail on PAUSE ---------------------------------
    p2 = mk()
    p2.play()
    time.sleep(0.8)
    p2.pause()
    time.sleep(0.05)
    s_pause = TailStream.instances[-1]
    pause_tail = len(s_pause.drained)
    p2.stop()

    print(f"write_sizes(sample)={s_close.write_sizes[:3]} "
          f"n_writes={len(s_close.write_sizes)}")
    print(f"[{'BAD' if close_tail else 'OK '}] RC5 close tail: "
          f"{close_tail} bytes (~{tail_ms(close_tail):.0f} ms) "
          f"played after close() aborted={s_close.aborted}")
    print(f"[{'BAD' if pause_tail else 'OK '}] RC5 pause tail: "
          f"{pause_tail} bytes (~{tail_ms(pause_tail):.0f} ms) "
          f"played after pause() aborted={s_pause.aborted}")
    if close_tail or pause_tail:
        print("RC5 CONFIRMED: buffered audio continues after pause/close "
              "(stop() drains the device buffer; abort() discards it).")
    else:
        print("RC5 NOT CONFIRMED: no post-pause/close tail measured.")


if __name__ == "__main__":
    main()