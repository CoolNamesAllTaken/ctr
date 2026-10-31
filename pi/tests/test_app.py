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
    while True:  # the audio thread appends while we read: copy it first (a copy can still race; retry)
        try:
            snap = list(written)
            break
        except RuntimeError:
            continue
    bt, bi = max((w for w in snap if w[0] is not None and w[0] <= t), key=lambda w: w[0])
    return bi * m.BLOCK + (t - bt)


def rms(x):
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


@pytest.fixture(scope="module")
def running():
    assert hw.FAKE and hw.usb_outputs() == [("Device", "1.2"), ("Device_1", "1.3")]
    m.cfg.data["direction"] = "A"  # car A leads: A ext plays the front mix, B ext the rear
    m.cfg.data["speakers"] = {"a_ext": {"device": "jack", "ch": "both", "level": 100},
                              "a_int": {"device": "usb:1.3", "ch": "right", "level": 100},
                              "b_ext": {"device": "usb#1", "ch": "left", "level": 50},
                              "b_int": {"device": "none", "ch": "both", "level": 100}}
    m.cfg.data["intercom"]["duck_db"] = -18
    m.cfg.save()
    if m.sidecar:
        m.sidecar.start()
        assert wait_for(lambda: m.sidecar.ready, 20), m.sidecar.error
    m.router.start()
    yield
    m.router.stop()
    if m.sidecar:
        m.sidecar.stop()


def test_speakers_route_to_their_card_and_channel(running):
    assert wait_for(lambda: set(m.router.devices) == {"jack", "usb:1.3", "usb:1.2"} and
                    all(d.ok for d in m.router.devices.values()))
    assert m.router.status["a_int"]["card"] == "Device_1" and m.router.status["b_ext"]["card"] == "Device"
    m.audio.hold("test", True)
    time.sleep(1.0)
    m.audio.hold("test", False)
    time.sleep(0.6)
    jack, _ = latest("jack")
    inside, _ = latest("usb-1.3")
    rear, _ = latest("usb-1.2")
    horn = rms(jack[:, 0])
    assert horn > 0.05 and rms(jack[:, 1]) == pytest.approx(horn, rel=1e-3)  # front: both channels
    assert rms(inside[:, 0]) == 0.0 and rms(inside[:, 1]) > 0.005  # inside: right only
    assert rms(rear[:, 1]) == 0.0 and rms(rear[:, 0]) > 0.0  # rear: left only
    if m.sidecar:  # v12: each speaker its own mix; no horn at the rear end, a muffled one inside
        assert rms(rear[:, 0]) < horn / 4 and rms(inside[:, 1]) < horn
    else:  # the old engine: the same mix everywhere
        assert rms(inside[:, 1]) == pytest.approx(horn, rel=0.05) and rms(rear[:, 0]) == pytest.approx(horn / 2, rel=0.05)


def test_intercom_plays_inside_and_ducks_the_train(running):
    # The INSIDE bus as the engine made it, before the duck: {engine sample clock of the block: samples}
    bus_inside, render = {}, m.audio.render_buses

    def recording_render():
        b = render()
        bus_inside[m.audio.t - m.BLOCK] = np.array(b["inside"], dtype=float)
        return b

    m.audio.render_buses = recording_render
    m.audio.hold("test", True)  # a steady horn to duck
    time.sleep(0.8)
    m.intercom.keep_marks = True
    t_claim = m.audio.t
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
    m.audio.render_buses = render
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
    # the train sound on INSIDE is ducked by 18 dB while talking: what the card played over the INSIDE bus the engine
    # made, block by block (the same samples, so the horn's own level changes don't matter; FRONT is no reference
    # with the v12 engine, its mix differs from INSIDE's and their ratio wanders by ~1.5 dB)
    written = list(m.router.devices["usb:1.3"].written)  # one copy: the router thread keeps appending
    blocks = {t: bi for t, bi in written if t is not None}

    def gain(s0, s1):  # engine sample clock window, relative to the claim
        ts = [t for t in bus_inside if t_claim + s0 * sr <= t and t + m.BLOCK <= t_claim + s1 * sr and t in blocks]
        out = np.concatenate([inside[blocks[t] * m.BLOCK:(blocks[t] + 1) * m.BLOCK, 1] for t in ts])
        ref = np.concatenate([bus_inside[t] for t in ts])
        assert len(ts) * m.BLOCK > 0.2 * sr and rms(ref) > 0.002
        return rms(out) / rms(ref)

    duck = 20 * np.log10(gain(0.1, 0.55) / gain(-0.6, -0.05))  # silence from the phone until 0.6 s
    assert duck == pytest.approx(-18, abs=0.3)
    # latency: each frame's capture time to when it played on the (fake) card
    lat = [i0 + file_pos(written, m.intercom.t0 + idx) / sr - t / 1000
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
    # board "A" comes with car A's lamps on GP0-GP3 (cars.py); these tests map pins themselves
    outs = m.cfg.data["boards"][UID]["outputs"]
    assert [o["fn"] for o in outs[:4]] == ["a_ditch_l", "a_ditch_r", "a_head", "a_markers"]
    for o in outs:
        o["fn"] = None
    m.cfg.save()
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


def test_doors_function_follows_the_doors(running, board):
    outs = m.cfg.data["boards"][UID]["outputs"]
    outs[2] = {"fn": "doors", "alt": False}
    m.cfg.save()
    m.audio.doors_cmd("open")  # the engine (running) moves the doors
    assert wait_for(lambda: m.audio.doors == "open", 12)
    assert wait_for(lambda: board.coils[2])
    m.audio.doors_cmd("close")
    assert wait_for(lambda: m.audio.doors == "closing", 3)
    # flashing while they move: the pin changes within a second
    seen = set()
    end = time.monotonic() + 1.2
    while time.monotonic() < end:
        seen.add(board.coils[2])
        time.sleep(0.05)
    assert seen == {True, False}


def test_flash_alternates_phases_and_relays(board):
    m.cfg.data["functions"].append({"id": "flashy", "name": "Flashy", "kind": "manual", "symbol": "●"})
    outs = m.cfg.data["boards"][UID]["outputs"]
    outs[0], outs[1] = {"fn": "flashy", "alt": False}, {"fn": "flashy", "alt": True}
    relay = m.cfg.data["relay"]
    saved_relay = [dict(o) for o in relay]
    relay[2], relay[3] = {"fn": "flashy", "alt": False}, {"fn": "flashy", "alt": True}
    m.cfg.save()
    m.io.set_mode("flashy", "flash")
    states = set()
    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        states.add((board.coils[0], board.coils[1], tuple(m.io.relay_state[2:4])))
        time.sleep(0.05)
    assert {(True, False), (False, True)} <= {s[:2] for s in states}  # A and B alternate
    assert {(True, False), (False, True)} <= {s[2] for s in states}  # relays R3/R4 as phases A/B
    m.io.set_mode("flashy", "off")
    relay[:] = saved_relay
    m.cfg.save()
    # relay 1 = car A's headlight, on while A leads: unmapping it hands it to the by-hand switch
    assert wait_for(lambda: m.io.relay_state[0])
    m.cfg.data["relay"][0] = {"fn": None, "alt": False}
    m.cfg.save()
    assert wait_for(lambda: not m.io.relay_state[0])
    m.io.toggle_output(("relay", 0))
    assert wait_for(lambda: m.io.relay_state[0])
    m.io.toggle_output(("relay", 0))
    m.cfg.data["relay"][0] = {"fn": "a_head", "alt": False}
    m.cfg.save()


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
