"""Cars A / B (caltreat.cars): direction rules, the reverser interlock, the Router feeding the four speakers on two
DACs by direction (and swapping smoothly), the engine's REAR bus without the horn, the per-car lights (wig-wag
timing, FRA option), and version 1 configs migrated."""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from caltreat import cars
from caltreat.config import Config, defaults, parse

SR = 48000
FAKE = Path(os.environ["CALTREAT_FAKE_AUDIO"])


# ---- rules ----
def test_roles_follow_the_direction():
    assert [cars.role(p, "A") for p in cars.PHYS] == ["front", "inside", "rear", "inside"]
    assert [cars.role(p, "B") for p in cars.PHYS] == ["rear", "inside", "front", "inside"]


def test_standstill():
    assert cars.standstill(0.0, 0.0)
    assert not cars.standstill(0.0, 0.2)  # power on
    assert not cars.standstill(2 / 79, 0.0)  # still rolling at 2 mph


def test_migrate_v1_configs():
    old_default = parse(json.dumps({"version": 1}))[0]
    assert old_default["speakers"] == cars.default_speakers() and "relay" not in old_default
    custom = {"version": 1, "speakers": {"front": {"device": "usb:1.2", "ch": "left"}, "inside": {"device": "usb:1.3"},
                                         "rear": {"device": "usb:1.2", "ch": "right", "level": 60}},
              "relay": [{"fn": "head"}, {"fn": None}, {"fn": "markers"}, {"fn": "markers", "alt": True}],
              "functions": [{"id": "head", "name": "Headlights", "kind": "manual"},
                            {"id": "markers", "name": "Markers", "kind": "manual"}]}
    new, errs = parse(json.dumps(custom))
    assert errs == []
    assert new["speakers"] == {"a_ext": {"device": "usb:1.2", "ch": "left", "level": 100},
                               "a_int": {"device": "usb:1.3", "ch": "both", "level": 100},
                               "b_ext": {"device": "usb:1.2", "ch": "right", "level": 60},
                               "b_int": {"device": "none", "ch": "both", "level": 100}}
    assert "relay" not in new and {"head", "markers", "a_head"} <= {f["id"] for f in new["functions"]}
    assert new["version"] == 4 and new["direction"] == "A"


def test_board_defaults_follow_the_board_name(tmp_path):
    from caltreat.config import board_defaults
    assert [o["fn"] for o in board_defaults("B")["outputs"]] == ["b_ditch_l", "b_ditch_r", "b_head", "b_markers",
                                                                 "stack_green", "stack_yellow", "stack_red",
                                                                 "stack_blue"]
    assert all(o["fn"] is None for o in board_defaults("C")["outputs"])


# ---- the reverser (engineer page) ----
@pytest.fixture
def app_module(monkeypatch):
    import caltreat.__main__ as m
    monkeypatch.setitem(m.cfg.data, "direction", "A")
    return m


@pytest.mark.parametrize("speed, throttle, seq, ok", [(0, 0, False, True), (0.2, 0, False, False),
                                                      (0, 0.3, False, False), (0, 0, True, False)])
def test_reverser_only_at_standstill(app_module, monkeypatch, speed, throttle, seq, ok):
    m = app_module
    monkeypatch.setattr(m, "audio", SimpleNamespace(motor=SimpleNamespace(speed=speed, throttle=throttle)))
    monkeypatch.setattr(m, "seq_status", lambda: {"active": seq})
    monkeypatch.setattr(m.cfg, "changed", lambda *a: None)  # no saving / listeners here
    m.dir_refused[0] = 0.0
    assert m.set_direction("B") is ok
    assert m.cfg.data["direction"] == ("B" if ok else "A")
    refused = time.monotonic() - m.dir_refused[0] < 2.5
    assert refused is (not ok)


# ---- speakers: the Router with a fake 3-bus engine on the two fake DACs ----
class Buses:
    """render_buses(): FRONT 500 Hz, INSIDE 700 Hz, REAR 900 Hz (amplitude 0.3)."""

    def __init__(self):
        self.t = 0

    def render_buses(self):
        n = np.arange(self.t, self.t + 512) / SR
        self.t += 512
        return {b: 0.3 * np.sin(2 * np.pi * f * n) for b, f in (("front", 500), ("inside", 700), ("rear", 900))}


def tones(x):
    """{500, 700, 900 Hz: amplitude} of a channel."""
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) / len(x) * 4
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return {k: round(float(s[np.argmin(abs(f - k))]), 2) for k in (500, 700, 900)}


def latest(name):
    raw = sorted(FAKE.glob(f"{name}-*.raw"), key=lambda p: p.stat().st_mtime)[-1]
    return np.frombuffer(raw.read_bytes()[: raw.stat().st_size // 4 * 4], "<i2").reshape(-1, 2) / 32768


def test_four_speakers_by_direction_and_smooth_swap(tmp_path):
    import caltreat.__main__ as m
    cfg = Config(defaults(), tmp_path)  # default speakers: DAC #1 L/R = A ext/int, DAC #2 L/R = B ext/int
    ic = SimpleNamespace(pull=lambda n, t=None: None, duck=lambda: 1.0)
    router = m.Router(Buses(), ic, cfg)
    router.start()
    try:
        time.sleep(1.0)
        a = {"a": latest("usb-1.2")[-SR // 2:], "b": latest("usb-1.3")[-SR // 2:]}
        cfg.data["direction"] = "B"
        time.sleep(1.0)
        b = {"a": latest("usb-1.2")[-SR // 2:], "b": latest("usb-1.3")[-SR // 2:]}
        whole = latest("usb-1.2")
    finally:
        router.stop()
    want = {500: 0.3, 700: 0.0, 900: 0.0}
    assert tones(a["a"][:, 0]) == want  # A fwd: A ext = FRONT (the horn's bus)
    assert tones(a["b"][:, 0]) == {500: 0.0, 700: 0.0, 900: 0.3}  # B ext = REAR
    assert tones(a["a"][:, 1]) == tones(a["b"][:, 1]) == {500: 0.0, 700: 0.3, 900: 0.0}  # both interiors = INSIDE
    assert tones(b["b"][:, 0]) == want and tones(b["a"][:, 0]) == {500: 0.0, 700: 0.0, 900: 0.3}  # swapped
    assert tones(b["a"][:, 1]) == {500: 0.0, 700: 0.3, 900: 0.0}
    # the swap is a crossfade: no step anywhere (a jump would be ~0.6; the tones alone move < 0.04 per sample)
    assert np.max(np.abs(np.diff(whole[:, 0]))) < 0.05


@pytest.mark.skipif(not __import__("caltreat.sidecar", fromlist=["find_node"]).find_node()[0],
                    reason="node 18+ not installed")
def test_engine_rear_bus_has_the_bell_but_not_the_horn():
    from caltreat.sidecar import BLOCK, Sidecar, find_node
    sc = Sidecar(find_node()[0], warmup=False)
    sc.start()
    end = time.monotonic() + 20
    while not sc.ready and time.monotonic() < end:
        time.sleep(0.02)
    assert sc.ready, sc.error
    sc.READ_TIMEOUT = 5.0
    sc.send({"t": "busConfig", "hornRoute": "front"})  # what the app sends (sidecar.apply_settings)
    t0 = 48 * BLOCK
    for at, msg in ((t0 + SR, {"t": "horn", "on": True}), (t0 + 3 * SR, {"t": "horn", "on": False}),
                    (t0 + 5 * SR, {"t": "bell", "on": True})):
        sc.send({**msg, "at": at})
    x = np.concatenate([sc.read_block().copy() for _ in range((t0 + 7 * SR) // BLOCK)])
    sc.stop()

    def band(a, b, i, f0, f1):  # amplitude in a band (the horn's fundamental ~376 Hz, the bell's partial ~1388 Hz)
        y = x[t0 + int(a * SR):t0 + int(b * SR), i]
        s = np.abs(np.fft.rfft(y * np.hanning(len(y)))) / len(y)
        f = np.fft.rfftfreq(len(y), 1 / SR)
        return float(np.sqrt(np.sum(s[(f > f0) & (f < f1)] ** 2)))

    horn = lambda a, b, i: band(a, b, i, 340, 420)  # noqa: E731
    bell = lambda a, b, i: band(a, b, i, 1360, 1420)  # noqa: E731
    assert horn(1.5, 2.8, 0) > 10 * horn(0.2, 0.9, 0)  # FRONT: the horn
    assert horn(1.5, 2.8, 2) < 2 * horn(0.2, 0.9, 2) + 1e-4  # REAR: no horn
    assert bell(5.5, 6.8, 2) > 10 * bell(0.2, 0.9, 2)  # REAR: the bell
    assert bell(5.5, 6.8, 0) > bell(5.5, 6.8, 2)  # louder at the FRONT (-6 dB at the REAR)


# ---- lights (49 CFR 229.125) ----
def fake_io(direction, mph=0.0, horn=False, crossing=False):
    import caltreat.__main__ as m
    cfg = Config(defaults(), "/nonexistent")
    cfg.data.update(direction=direction)
    cfg.data["boards"] = {"AA01": {"name": "A", "outputs": cars.default_board_outputs("A", 8)},
                          "BB02": {"name": "B", "outputs": cars.default_board_outputs("B", 8)}}
    audio = SimpleNamespace(horn_on=horn, motor=SimpleNamespace(speed=mph / 79, throttle=0.0), doors="closed",
                            busy_pattern="crossing" if crossing else None, bell=SimpleNamespace(on=False))
    finder = SimpleNamespace(found=[SimpleNamespace(uid="AA01"), SimpleNamespace(uid="BB02")])
    return m.Io(audio, cfg, finder, lambda: {})


def outputs(io, now):
    boards = io.desired(now)
    return boards["AA01"][:4], boards["BB02"][:4]  # ditch L, ditch R, headlight, markers


def ditch_seen(io, t0, secs=2.0):
    return {tuple(outputs(io, t0 + t / 10)[0][:2]) for t in range(int(secs * 10))}


@pytest.mark.parametrize("mph", [0, 30])
def test_a_leading_steady_moving_or_not(mph):
    io = fake_io("A", mph=mph)
    a, b = outputs(io, 100.0)
    assert a == [True, True, True, False] and b == [False, False, False, True]
    assert ditch_seen(io, 100.0) == {(True, True)}  # steady: no wig-wag just for moving


def test_ditch_lights_flash_with_the_horn_then_hold():
    io = fake_io("A", mph=30, horn=True)
    assert ditch_seen(io, 100.0) == {(True, False), (False, True)}  # alternate, never both
    # 60 / min each: L on for 0.5 s, then R
    assert outputs(io, 100.0)[0][:2] == [True, False] and outputs(io, 100.5)[0][:2] == [False, True]
    io.audio.horn_on = False  # horn released at t = 101.9 (last seen): keeps flashing for hold_s = 15 s
    assert ditch_seen(io, 110.0) == {(True, False), (False, True)}
    assert ditch_seen(io, 118.0) == {(True, True)}
    io.cfg.data["ditch"]["hold_s"] = 30
    assert ditch_seen(io, 125.0) == {(True, False), (False, True)}


def test_ditch_lights_flash_through_a_crossing():
    io = fake_io("A", mph=30, crossing=True)
    assert ditch_seen(io, 100.0) == {(True, False), (False, True)}


def test_b_leading_and_master_off():
    io = fake_io("B", mph=20, horn=True)
    a, b = outputs(io, 100.0)
    assert a == [False, False, False, True] and b[2:] == [True, False]
    assert ditch_seen(io, 100.0) == {(False, False)}  # the trailing car's ditch lights stay off
    io.cfg.data["lights_on"] = False
    a, b = outputs(io, 100.0)
    assert not any(a) and not any(b)


def test_flash_rate_setting():
    io = fake_io("A", horn=True)
    io.cfg.data["ditch"]["rate"] = 120  # 120 / min: 0.25 s halves
    assert outputs(io, 100.0)[0][:2] == [True, False] and outputs(io, 100.25)[0][:2] == [False, True]
