"""The app's engine without a Pi: speaker routing to (fake) sound cards, intercom ducking, and light mappings reaching
the right relay channel and lighting controller pin (fake Modbus board, fake_board.py)."""

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest
from fake_board import FakeBoard

import caltreat.__main__ as m
from caltreat import hw
from caltreat.intercom import HEADER

FAKE = Path(os.environ["CALTREAT_FAKE_AUDIO"])
UID = "E6614103E7A1B2C3"


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def latest(name):
    """The newest fake player recording for a device: (frames [n, 2] float, start time)."""
    raws = sorted(FAKE.glob(f"{name}-*.raw"), key=lambda p: p.stat().st_mtime)
    raw = raws[-1]
    meta = json.loads(raw.with_suffix(".json").read_text())
    x = np.frombuffer(raw.read_bytes()[: raw.stat().st_size // 4 * 4], dtype="<i2").reshape(-1, 2) / 32768.0
    return x, meta["start"]


def file_pos(written, t):
    """Engine sample clock -> sample index in a fake card's recording (blocks dropped or padded are accounted)."""
    bt, bi = max((w for w in written if w[0] is not None and w[0] <= t), key=lambda w: w[0])
    return bi * m.BLOCK + (t - bt)


def rms(x):
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


@pytest.fixture(scope="module")
def running():
    assert hw.FAKE and hw.usb_outputs() == [("Device", "1.2"), ("Device_1", "1.3")]
    m.cfg.data["speakers"] = {"front": {"device": "jack", "ch": "both", "level": 100},
                              "inside": {"device": "usb:1.3", "ch": "right", "level": 100},
                              "rear": {"device": "usb#1", "ch": "left", "level": 50}}
    m.cfg.data["intercom"]["duck_db"] = -18
    m.cfg.save()
    m.router.start()
    yield
    m.router.stop()


def test_speakers_route_to_their_card_and_channel(running):
    assert wait_for(lambda: set(m.router.devices) == {"jack", "usb:1.3", "usb:1.2"} and
                    all(d.ok for d in m.router.devices.values()))
    assert m.router.status["inside"]["card"] == "Device_1" and m.router.status["rear"]["card"] == "Device"
    m.audio.hold("test", True)
    time.sleep(1.0)
    m.audio.hold("test", False)
    time.sleep(0.6)
    jack, _ = latest("jack")
    inside, _ = latest("usb-1.3")
    rear, _ = latest("usb-1.2")
    horn = rms(jack[:, 0])
    assert horn > 0.05 and rms(jack[:, 1]) == pytest.approx(horn, rel=1e-3)  # front: both channels
    assert rms(inside[:, 0]) == 0.0 and rms(inside[:, 1]) == pytest.approx(horn, rel=0.05)  # inside: right only
    assert rms(rear[:, 1]) == 0.0 and rms(rear[:, 0]) == pytest.approx(horn / 2, rel=0.05)  # rear: left at 50 %


def test_intercom_plays_inside_and_ducks_the_train(running):
    m.audio.hold("test", True)  # a steady horn to duck
    time.sleep(0.8)
    m.intercom.keep_marks = True
    assert m.intercom.claim("tok", "page")
    # from the "phone": 0.6 s of silence (to measure the duck), then 1 s of 1 kHz
    tone = 0.3 * np.sin(2 * np.pi * 1000 * np.arange(160 * 160) / 16000)
    tone[:160 * 60] = 0
    t_talk = time.time()
    for i in range(160):  # 10 ms frames, as the page sends
        pcm = (tone[i * 160:(i + 1) * 160] * 32767).astype("<i2").tobytes()
        m.intercom.feed("tok", HEADER.pack(i, (t_talk + i * 0.01) * 1000) + pcm)
        time.sleep(0.01)
    m.intercom.release("tok")
    time.sleep(0.6)
    m.audio.hold("test", False)
    time.sleep(0.3)
    jack, j0 = latest("jack")
    inside, i0 = latest("usb-1.3")
    sr = 48000

    def seg(x, start, t0, t1):  # samples between wall-clock times t0..t1
        return x[max(0, int((t0 - start) * sr)): max(0, int((t1 - start) * sr))]

    # the 1 kHz intercom tone is on the inside right channel, not on the front
    talk = seg(inside[:, 1], i0, t_talk + 0.9, t_talk + 1.5)
    spec = np.abs(np.fft.rfft(talk * np.hanning(len(talk))))
    f = np.fft.rfftfreq(len(talk), 1 / sr)
    assert f[np.argmax(spec)] == pytest.approx(1000, abs=5)
    front = seg(jack[:, 0], j0, t_talk + 0.9, t_talk + 1.5)
    fs = np.abs(np.fft.rfft(front * np.hanning(len(front))))
    assert fs[np.argmin(abs(f - 1000))] < 0.01 * spec.max()
    # the horn on INSIDE is ducked by 18 dB while talking: compare its own band (the horn's 2nd harmonic ~750 Hz)
    def band(x, lo=600, hi=900):
        s = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        ff = np.fft.rfftfreq(len(x), 1 / sr)
        return float(np.sqrt(np.sum(s[(ff > lo) & (ff < hi)] ** 2))) / len(x)  # amplitude, any window length
    before = band(seg(inside[:, 1], i0, t_talk - 0.6, t_talk - 0.1))
    during = band(seg(inside[:, 1], i0, t_talk + 0.25, t_talk + 0.55))
    assert 20 * np.log10(during / before) == pytest.approx(-18, abs=1.5)
    # latency: each frame's capture time to when it played on the (fake) card
    lat = [i0 + file_pos(m.router.devices["usb:1.3"].written, m.intercom.t0 + idx) / sr - t / 1000
           for idx, t, *_ in m.intercom.marks]
    assert lat and 0 < np.median(lat) < 0.15, np.median(lat)


def test_speaker_test_tone_on_one_channel(running):
    m.router.set_test("usb:1.2", "right")
    time.sleep(0.6)
    m.router.set_test(None, None)
    time.sleep(0.2)
    rear, _ = latest("usb-1.2")
    right = rear[:, 1]
    assert rms(right) > 0.05  # rear is mapped to the left: only the tone is on the right
    s = np.abs(np.fft.rfft(right))
    assert np.fft.rfftfreq(len(right), 1 / 48000)[np.argmax(s)] == pytest.approx(440, abs=3)


@pytest.fixture(scope="module")
def board():
    b = FakeBoard("127.0.1.10", int(os.environ["CALTREAT_BOARD_PORT"]), uid=UID).start()
    m.finder.start()
    m.io.start()
    assert wait_for(lambda: any(x.uid == UID and x.connected for x in m.finder.found), 15)
    yield b
    m.io.stop()
    m.finder.stop()
    b.stop()


def test_new_board_named_and_saved(board):
    rb = next(x for x in m.finder.found if x.uid == UID)
    assert rb.name == "A" and rb.mac == "28:CD:C1:0A:0B:0C"
    saved = json.loads(m.cfg.path.read_text())
    assert saved["boards"][UID]["name"] == "A" and len(saved["boards"][UID]["outputs"]) == 8


def test_mapping_reaches_the_right_pin(board):
    outs = m.cfg.data["boards"][UID]["outputs"]
    outs[5] = {"fn": "interior", "alt": False}
    m.cfg.save()
    m.io.set_mode("interior", "on")
    assert wait_for(lambda: board.coils[5])
    assert not any(board.coils[i] for i in range(8) if i != 5)
    # remap interior from GP5 to GP6: the hardware follows without code changes
    outs[5], outs[6] = {"fn": None, "alt": False}, {"fn": "interior", "alt": False}
    m.cfg.save()
    assert wait_for(lambda: board.coils[6] and not board.coils[5])
    m.io.set_mode("interior", "off")
    assert wait_for(lambda: not board.coils[6])


def test_doors_function_follows_the_doors(board):
    outs = m.cfg.data["boards"][UID]["outputs"]
    outs[2] = {"fn": "doors", "alt": False}
    m.cfg.save()
    m.audio.doors_cmd("open")
    for _ in range(300):  # ~3 s of engine time: the doors open fully
        m.audio.render()
    assert m.audio.doors == "open"
    assert wait_for(lambda: board.coils[2])
    m.audio.doors_cmd("close")
    for _ in range(20):
        m.audio.render()
    assert m.audio.doors == "closing"
    # flashing while they move: the pin changes within a second
    seen = set()
    end = time.monotonic() + 1.2
    while time.monotonic() < end:
        seen.add(board.coils[2])
        time.sleep(0.05)
    assert seen == {True, False}


def test_flash_alternates_phases_and_relays(board):
    outs = m.cfg.data["boards"][UID]["outputs"]
    outs[0], outs[1] = {"fn": "markers", "alt": False}, {"fn": "markers", "alt": True}
    m.cfg.save()
    m.io.set_mode("markers", "flash")
    states = set()
    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        states.add((board.coils[0], board.coils[1], tuple(m.io.relay_state[2:4])))
        time.sleep(0.05)
    assert {(True, False), (False, True)} <= {s[:2] for s in states}  # A and B alternate
    assert {(True, False), (False, True)} <= {s[2] for s in states}  # default relays R3/R4 = markers A/B
    m.io.set_mode("markers", "off")
    # relay 1 = headlights A: unmapping it hands it to the by-hand switch
    m.io.set_mode("head", "on")
    assert wait_for(lambda: m.io.relay_state[0])
    m.cfg.data["relay"][0] = {"fn": None, "alt": False}
    m.cfg.save()
    assert wait_for(lambda: not m.io.relay_state[0])
    m.io.toggle_output(("relay", 0))
    assert wait_for(lambda: m.io.relay_state[0])
    m.io.toggle_output(("relay", 0))
    m.io.set_mode("head", "off")


def test_force_test_and_unmapped_toggle(board):
    m.io.toggle_output((UID, 7))  # unmapped: by hand
    assert wait_for(lambda: board.coils[7])
    m.io.all_off(UID)
    assert wait_for(lambda: not board.coils[7])
    m.io.toggle_output((UID, 6))  # mapped to interior (off): forced on as a test
    assert wait_for(lambda: board.coils[6])
    m.io.toggle_output((UID, 6))
    assert wait_for(lambda: not board.coils[6])


def test_stop_brakes_to_a_halt():
    a = m.AudioOut()
    a.set_timing("costume")
    a.set_throttle(0.5)
    for _ in range(int(8 * 48000 / 512)):
        a.render()
    assert a.motor.speed == pytest.approx(0.5)
    a.stop_train()
    n = 0
    while a.motor.speed > 0 and n < 2000:
        a.render()
        n += 1
    t = n * 512 / 48000
    assert t == pytest.approx(0.5 * m.BRAKE_TIME, abs=0.2)  # braking, not coasting (30 s from full)
    a.render()  # standing: the air release is scheduled
    assert a.squeal_done and a.air_at is not None and not a.braking  # squeal near the end, then the air
