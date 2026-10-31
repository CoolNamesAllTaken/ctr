"""Intercom: one talker, jitter buffer, upsampling, timeouts, ducking."""

import time

import numpy as np

from caltreat import intercom as icm
from caltreat.intercom import FRAME, HEADER, Intercom


def frames(x, t0=0.0):
    """16 kHz float signal -> wire frames."""
    out = []
    for i in range(0, len(x) - FRAME + 1, FRAME):
        pcm = (np.clip(x[i:i + FRAME], -1, 1) * 32767).astype("<i2").tobytes()
        out.append(HEADER.pack(i // FRAME, t0 + i / 16.0) + pcm)
    return out


def settings(**kw):
    s = {"mic_gain_db": 0, "level": 100, "duck_db": -20, "timeout_s": 60}
    s.update(kw)
    return lambda: s


def test_one_talker_at_a_time():
    ic = Intercom(settings())
    assert ic.claim("a", "phone-1")
    assert not ic.claim("b", "phone-2")
    assert ic.who == "phone-1"
    assert not ic.feed("b", frames(np.zeros(FRAME))[0])  # not the talker
    ic.release("b")  # someone else can't release it
    assert ic.talking
    ic.release("a")
    assert ic.claim("b")


def test_audio_comes_out_upsampled_and_in_order():
    ic = Intercom(settings())
    ic.claim("a")
    spec = np.fft.rfft(np.random.default_rng(1).normal(0, 0.15, 16000))
    spec[np.fft.rfftfreq(16000, 1 / 16000) > 6000] = 0
    x = np.fft.irfft(spec, 16000)  # 1 s of noise below 6 kHz: one clear correlation peak
    out = []
    for f in frames(x):  # in real time: one 10 ms frame in, 10 ms (480 samples at 48 kHz) out
        ic.feed("a", f)
        out.append(ic.pull(3 * FRAME))
    ic.release("a")  # what is buffered still plays out
    while (y := ic.pull(3 * FRAME)) is not None:
        out.append(y)
    y = np.concatenate(out)
    assert ic.stats["underruns"] == 0 and ic.stats["dropped_ms"] == 0
    ref = np.fft.irfft(np.concatenate([np.fft.rfft(x), np.zeros(16000)]), 48000) * 3  # x band-limited at 48 kHz
    lag = np.argmax(np.correlate(y[:30000], ref[:20000], mode="valid"))
    # playing starts at the 2nd 10 ms frame (the 20 ms prebuffer); the FIR adds 48 samples (1 ms)
    assert 480 <= lag < 480 + 60
    n = min(len(y) - lag, len(ref))
    c = np.corrcoef(y[lag:lag + n], ref[:n])[0, 1]
    assert c > 0.99
    assert abs(np.std(y[lag:lag + n]) / np.std(ref[:n]) - 1) < 0.05  # unity gain


def test_jitter_buffer_waits_then_plays_and_grows():
    ic = Intercom(settings())
    ic.claim("a")
    assert not np.any(ic.pull(480))  # nothing yet: silence while talking
    f = frames(np.full(320 * 10, 0.5))
    ic.feed("a", f[0])  # 10 ms < the 20 ms prebuffer: waits
    assert not np.any(ic.pull(480))
    ic.feed("a", f[1])  # 20 ms: plays
    assert np.mean(ic.pull(960)[480:]) > 0.4  # (after the interpolation filter's 1 ms onset)
    assert not np.any(ic.pull(480))  # ran dry (the next frame is late): an underrun
    assert ic.stats["underruns"] == 1 and ic.prebuf == icm.PREBUF_MS + icm.PREBUF_STEP
    ic.feed("a", f[2])
    ic.feed("a", f[3])  # 20 ms < 30 ms: waits now
    assert not np.any(ic.pull(480))
    ic.feed("a", f[4])
    assert np.any(ic.pull(480))


def test_start_burst_is_kept():
    ic = Intercom(settings())
    ic.claim("a")
    for f in frames(np.full(8000, 0.1)):  # 0.5 s queued by the page while the talk started, arriving at once
        ic.feed("a", f)
    assert ic.stats["dropped_ms"] == 0 and len(ic.buf) == 8000  # the first words are not cut


def test_overfull_buffer_is_cut_back():
    ic = Intercom(settings())
    ic.claim("a")
    ic.started -= icm.SETTLE + 0.5  # steady state: talking for a while
    for f in frames(np.full(16000, 0.1)):  # 1 s at once (a stalled network catching up)
        ic.feed("a", f)
    assert ic.stats["dropped_ms"] > 800
    assert len(ic.buf) * 1000 / 16000 <= icm.MAX_MS


def test_lost_frames_counted():
    ic = Intercom(settings())
    ic.claim("a")
    f = frames(np.zeros(FRAME * 5))
    for i in (0, 1, 4):
        ic.feed("a", f[i])
    assert ic.stats["lost"] == 2 and ic.stats["frames"] == 3


def test_timeout_and_stale(monkeypatch):
    ic = Intercom(settings(timeout_s=5))
    ic.claim("a")
    assert ic.expired() is None
    ic.started -= 6
    assert ic.expired() == "timeout"
    ic.claim("b") if not ic.talking else None
    ic.release("a")
    ic.claim("b")
    ic.last_frame = time.monotonic() - icm.STALE - 0.1
    assert ic.expired() == "stale"


def test_gain_and_duck():
    ic = Intercom(settings(mic_gain_db=6, level=50, duck_db=-20))
    assert ic.duck() == 1.0
    ic.claim("a")
    assert abs(ic.duck() - 0.1) < 1e-9
    for f in frames(np.full(FRAME * 6, 0.2)):
        ic.feed("a", f)
    y = ic.pull(960)
    assert abs(y[-1] - 0.2 * 10 ** (6 / 20) * 0.5) < 0.01


def test_latency_marks():
    ic = Intercom(settings())
    ic.keep_marks = True
    ic.claim("a")
    for f in frames(np.full(FRAME * 3, 0.1), t0=1000.0):
        ic.feed("a", f)
    ic.pull(512)
    ic.pull(512)
    assert [m[1] for m in ic.marks[:2]] == [1000.0, 1010.0]
    assert ic.marks[0][0] == 0 and ic.marks[1][0] == 480  # 10 ms apart at 48 kHz, from the stream's start
