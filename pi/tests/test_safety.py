"""No relay shield (config version 3), the walkers' stack lights and the E-STOP alarm tone, without the app running."""

import itertools
import json
from types import SimpleNamespace

import numpy as np
import pytest

from caltreat import cars, safety
from caltreat.config import Config, board_defaults, defaults, parse, validate
from caltreat.sequencer import Sequencer

V2_BOARD = {"name": "A", "outputs": [{"fn": "a_ditch_l", "alt": False}, {"fn": "a_ditch_r", "alt": False},
                                     {"fn": "a_head", "alt": False}, {"fn": "a_markers", "alt": False}]
            + [{"fn": None, "alt": False}] * 4}


def v2(**kw):
    c = {"version": 2, "relay": [dict(o) for o in cars.DEFAULT_RELAY],
         "boards": {"AA01": json.loads(json.dumps(V2_BOARD)),
                    "BB02": {**json.loads(json.dumps(V2_BOARD).replace('"a_', '"b_')), "name": "B"}}}
    c.update(kw)
    return c


# ---- config: the relay shield is gone ----
def test_defaults_have_no_relays_and_the_stack_on_gp4_7():
    d = defaults()
    assert "relay" not in d and d["version"] == 3 and validate(d) == []
    assert {"stack_green", "stack_yellow", "stack_red", "stack_blue"} <= {f["id"] for f in d["functions"]}
    assert [o["fn"] for o in board_defaults("A")["outputs"]] == [
        "a_ditch_l", "a_ditch_r", "a_head", "a_markers", "stack_green", "stack_yellow", "stack_red", "stack_blue"]


def test_v2_default_relays_drop_and_boards_get_the_stack():
    new, errs = parse(json.dumps(v2()))
    assert errs == [] and new["version"] == 3 and "relay" not in new
    for uid, car in (("AA01", "a"), ("BB02", "b")):
        assert [o["fn"] for o in new["boards"][uid]["outputs"]] == [
            f"{car}_ditch_l", f"{car}_ditch_r", f"{car}_head", f"{car}_markers",
            "stack_green", "stack_yellow", "stack_red", "stack_blue"]


def test_v2_custom_relays_move_to_free_board_pins():
    c = v2(relay=[{"fn": "a_head"}, {"fn": "interior", "alt": True}, {"fn": "b_markers"}, {"fn": "smoke"}],
           functions=[{"id": "smoke", "name": "Smoke", "kind": "manual"}])
    c["boards"]["CC03"] = {"name": "C", "outputs": [{"fn": None, "alt": False}] * 8}
    c["boards"]["AA01"]["outputs"][5] = {"fn": "doors", "alt": False}  # GP5 taken: stack yellow can't go there
    notes = []
    from caltreat.config import complete
    new = complete(c, notes)
    assert validate(new) == []
    a, cc = new["boards"]["AA01"]["outputs"], new["boards"]["CC03"]["outputs"]
    assert [o["fn"] for o in a] == ["a_ditch_l", "a_ditch_r", "a_head", "a_markers", "stack_green", "doors",
                                    "stack_red", "stack_blue"]
    # a_head / b_markers already on board pins: dropped; interior + smoke go to board C's free pins, phase kept
    assert cc[0] == {"fn": "interior", "alt": True} and cc[1] == {"fn": "smoke", "alt": False}
    assert any("GP5 is in use" in n for n in notes) and any("R2 (interior) -> board C GP0" in n for n in notes)


def test_v2_relays_with_no_free_pin_are_reported():
    c = v2(relay=[{"fn": "interior"}, {}, {}, {}])
    notes = []
    from caltreat.config import complete
    complete(c, notes)
    assert any("R1 (interior): no free board pin" in n for n in notes)


def test_v2_file_is_migrated_on_load_and_kept(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps(v2()))
    cfg = Config.load(tmp_path)
    assert cfg.error is None and cfg.data["version"] == 3
    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk["version"] == 3 and "relay" not in on_disk
    assert json.loads((tmp_path / "config.json.v2").read_text())["relay"] == cars.DEFAULT_RELAY


# ---- the stack state machine ----
def test_raw_states():
    assert safety.raw_state(0, 0) == "red"
    assert safety.raw_state(0, 0.5) == "green"  # power applied from a stand: starting to move
    assert safety.raw_state(0.3, 0.5) == "green"  # accelerating
    assert safety.raw_state(0.5, 0.5) == "green"  # cruising
    assert safety.raw_state(0.5, 0.3) == "yellow"  # power reduced: slowing to the new speed
    assert safety.raw_state(0.5, 0) == "yellow"  # coasting / braking with the power off
    assert safety.raw_state(0.5, 0.5, stopping=True) == "yellow"  # STOP
    assert safety.raw_state(0.001, 0) == "red"  # practically stopped
    assert safety.raw_state(0, 0.5, doors="open") == "red"  # doors open
    assert safety.raw_state(0.4, 0.5, ready=False) == "red"  # engine not running: unknown = stopped


def run(stack, seq, dt=0.05):
    """[(seconds, speed, throttle, stopping)] -> [(t, shown)]."""
    t, out = 0.0, []
    for secs, sp, th, stp in seq:
        end = t + secs
        while t < end - 1e-9:
            out.append((round(t, 3), stack.update(t, sp, th, stp)))
            t += dt
    return out


def test_power_on_is_red_then_green_after_the_hold():
    s = safety.Stack()
    assert s.state == "red" and s.lamps(0) == {"green": False, "yellow": False, "red": True}
    out = run(s, [(1.0, 0.0, 0.5, False)])
    first_green = next(t for t, x in out if x == "green")
    assert first_green == pytest.approx(0.5, abs=0.06)  # 0.5 s hold before going greener


def test_accelerate_power_off_slow_stop():
    s = safety.Stack()
    out = run(s, [(1, 0.0, 0.6, False), (2, 0.4, 0.6, False), (1, 0.6, 0.6, False),
                  (2, 0.4, 0.0, False), (1, 0.0, 0.0, False)])
    states = [x for _, x in out]
    seen = [states[0]] + [b for a, b in itertools.pairwise(states) if a != b]
    assert seen == ["red", "green", "yellow", "red"]
    # power off -> yellow at once (more restrictive: no hold), stopped -> red at once
    assert dict(out)[4.0] == "yellow" and dict(out)[6.0] == "red"


def test_no_flicker_on_a_jittery_throttle():
    s = safety.Stack()
    run(s, [(2, 0.5, 0.5, False)])
    assert s.state == "green"
    # power flickers between reduced and full every 0.2 s: yellow at once, and it stays yellow
    seq = [(0.2, 0.5, 0.3 if i % 2 == 0 else 0.5, False) for i in range(10)]
    out = run(s, seq)
    assert {x for _, x in out[1:]} == {"yellow"}


def test_estop_overrides_and_flashes():
    s = safety.Stack()
    run(s, [(2, 0.5, 0.5, False)])
    assert s.update(2.0, 0.5, 0.5, estop=True) == "estop"  # at once
    lamps = [s.lamps(2.0 + i * 0.05) for i in range(20)]
    assert not any(x["green"] or x["yellow"] for x in lamps)
    assert {x["red"] for x in lamps} == {True, False}  # flashing, not steady
    assert s.update(2.1, 0.0, 0.0) == "estop"  # reset: red comes only after the hold (greener)
    assert s.update(2.7, 0.0, 0.0) == "red"


class Train:
    """A tiny train for the sequencer: throttle / stop messages -> speed, as the engine does (roughly)."""

    def __init__(self):
        self.speed, self.throttle, self.stopping, self.doors = 0.0, 0.0, False, "closed"

    def send(self, m):
        if m["t"] == "throttle":
            self.throttle = m["v"]
            self.stopping = self.stopping and self.throttle <= 0.01
        elif m["t"] == "stop":
            self.stopping, self.throttle = self.speed > 0.003, 0.0
        elif m["t"] == "doors":
            self.doors = "open" if m["v"] == "open" else "closed"

    def step(self, dt):
        if self.stopping:
            self.speed = max(0.0, self.speed - dt * 0.08)
            self.stopping = self.speed > 0
        elif self.throttle > self.speed:
            self.speed = min(self.throttle, self.speed + dt / 4)
        else:
            self.speed = max(self.throttle, self.speed - dt / 8)


@pytest.mark.parametrize("name", ["arrive", "stop"])
def test_arrive_and_stop_brake_yellow_then_red(name):
    tr, s = Train(), safety.Stack()
    q = Sequencer(tr.send)
    t, seen = 0.0, []
    tr.send({"t": "throttle", "v": 0.4})
    for _ in range(int(4 / 0.05)):  # up to speed
        t += 0.05
        tr.step(0.05)
        q.tick(t, {"speed": tr.speed, "doors": tr.doors})
        s.update(t, tr.speed, tr.throttle, tr.stopping, tr.doors)
    assert s.state == "green" and tr.speed > 0.3
    if name == "arrive":
        q.start("arrive")
    else:
        tr.send({"t": "stop"})
    for _ in range(int(40 / 0.05)):
        t += 0.05
        tr.step(0.05)
        q.tick(t, {"speed": tr.speed, "doors": tr.doors})
        st = s.update(t, tr.speed, tr.throttle, tr.stopping, tr.doors)
        if not seen or seen[-1] != st:
            seen.append(st)
    assert seen == ["green", "yellow", "red"] if name == "arrive" else seen == ["yellow", "red"]
    if name == "arrive":
        assert tr.doors == "open" and s.state == "red"  # doors open at the platform: red


# ---- lights: the stack on the boards ----
def fake_io(estop=False, **audio):
    import caltreat.__main__ as m
    cfg = Config(defaults(), "/nonexistent")
    cfg.data["boards"] = {"AA01": board_defaults("A"), "BB02": board_defaults("B")}
    a = SimpleNamespace(horn_on=False, motor=SimpleNamespace(speed=0.0, throttle=0.0), doors="closed",
                        busy_pattern=None, bell=SimpleNamespace(on=False), stopping=False, ready=True)
    for k, v in audio.items():
        setattr(a.motor if k in ("speed", "throttle") else a, k, v)
    finder = SimpleNamespace(found=[SimpleNamespace(uid="AA01"), SimpleNamespace(uid="BB02")])
    flag = {"on": estop}
    return m.Io(a, cfg, finder, lambda: {}, lambda: flag["on"]), flag


def stack_pins(io, now):
    b = io.desired(now)
    return b["AA01"][4:8], b["BB02"][4:8]  # green, yellow, red, blue


def test_both_boards_show_the_stack():
    io, flag = fake_io()
    a, b = stack_pins(io, 10.0)
    assert a == b == [False, False, True, False]  # power-on: red
    io.audio.motor.throttle = 0.5
    stack_pins(io, 10.1)
    a, b = stack_pins(io, 10.7)
    assert a == b == [True, False, False, False]  # green
    io.audio.motor.speed, io.audio.motor.throttle = 0.5, 0.0
    a, b = stack_pins(io, 10.8)
    assert a == b == [False, True, False, False]  # yellow at once
    flag["on"] = True
    reds = {tuple(stack_pins(io, 11 + i * 0.05)[0]) for i in range(20)}
    assert reds == {(False, False, True, False), (False, False, False, False)}  # E-STOP: red flashing only


def test_blue_is_a_manual_light_default_off():
    io, _ = fake_io()
    assert stack_pins(io, 1.0)[0][3] is False
    io.cycle("stack_blue")
    assert stack_pins(io, 1.0)[0][3] is True and stack_pins(io, 1.0)[1][3] is True


def test_failsafe_levels_red_on_other_colours_off():
    io, _ = fake_io()
    assert io.failsafe_levels("AA01") == [None, None, None, None, False, False, True, False]
    io.cfg.data["boards"]["AA01"]["outputs"][0] = {"fn": "stack_red", "alt": False}  # remapped: follows
    assert io.failsafe_levels("AA01")[0] is True


# ---- the alarm ----
def band(y, sr, f0, f1):
    s = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    f = np.fft.rfftfreq(len(y), 1 / sr)
    return float(np.sqrt(np.sum(s[(f > f0) & (f < f1)] ** 2)))


def test_alarm_two_tone_loud_then_quiet_bursts():
    sr = 48000
    a = safety.Alarm(sr, lambda: {"level": 80, "quiet": 40, "loud_s": 2})
    assert a.render(512) is None
    a.start()
    y = np.concatenate([a.render(512) for _ in range(int(9 * sr / 512))])
    first, second = y[int(0.02 * sr):int(0.23 * sr)], y[int(0.27 * sr):int(0.48 * sr)]
    assert band(first, sr, 950, 1050) > 10 * band(first, sr, 750, 850)  # 1 kHz, then
    assert band(second, sr, 750, 850) > 10 * band(second, sr, 950, 1050)  # 800 Hz
    loud = np.max(np.abs(y[:2 * sr]))
    assert 0.6 < loud < 0.8
    quiet = y[2 * sr:]
    assert np.max(np.abs(quiet[:sr])) == pytest.approx(loud / 2, rel=0.05)  # a quiet burst right after
    assert np.max(np.abs(quiet[int(1.1 * sr):int(2.9 * sr)])) == 0.0  # then a pause
    assert np.max(np.abs(quiet[int(3.0 * sr):int(4.0 * sr)])) > 0.3  # next burst 3 s later
    edges = np.arange(1, 8) * int(safety.Alarm.HALF * sr)  # tone changes: faded through zero (no clicks)
    assert max(np.max(np.abs(y[e - 3:e + 3])) for e in edges) < 0.1
    a.stop()
    assert a.render(512) is None


def test_alarm_test_burst_ends_by_itself():
    a = safety.Alarm(48000, lambda: {"level": 80, "quiet": 40, "loud_s": 5})
    a.test(0.5)
    n = 0
    while a.render(512) is not None:
        n += 1
    assert n == pytest.approx(0.5 * 48000 / 512, abs=2) and not a.on
