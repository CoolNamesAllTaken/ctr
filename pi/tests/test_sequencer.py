"""Sequence timings (sequencer.py) against the rules in caltrain-sounds docs/procedures.md (v12): GCOR 8th ed. 5.8.1 /
5.8.2 and 49 CFR 222.21. A small simulated train answers the sequencer's messages."""

import pytest

from caltreat.sequencer import TIMINGS, Sequencer, pattern_len, signal_pattern, steps

DT = 0.05


class Train:
    """Speed follows the throttle (accel s to full), STOP brakes (brake s from full), doors take 3.5 s to close
    (warning, then the leaves) and 2.5 s to open, as AudioOut does."""

    def __init__(self, accel=14.0, brake=10.0, doors="closed"):
        self.accel, self.brake = accel, brake
        self.speed, self.throttle, self.braking = 0.0, 0.0, False
        self.doors, self.door_t = doors, 0.0
        self.bell = False
        self.log = []  # (t, message)
        self.t = 0.0

    def send(self, m):
        self.log.append((round(self.t, 3), m))
        if m["t"] == "throttle":
            self.throttle, self.braking = m["v"], False
        elif m["t"] == "stop":
            self.throttle, self.braking = 0.0, True
        elif m["t"] == "doors":
            self.doors, self.door_t = ("closing" if m["v"] == "close" else "opening"), self.t
        elif m["t"] == "bell":
            self.bell = m["on"]

    def step(self):
        self.t += DT
        if self.throttle > self.speed:
            self.speed = min(self.throttle, self.speed + DT / self.accel)
        else:
            self.speed = max(self.throttle, self.speed - DT / (self.brake if self.braking else 30.0))
        if self.doors == "closing" and self.t - self.door_t >= 3.5:
            self.doors = "closed"
        if self.doors == "opening" and self.t - self.door_t >= 2.5:
            self.doors = "open"

    def state(self):
        return {"speed": self.speed, "doors": self.doors}


def run(name, mode="costume", until=200.0, train=None, **opt):
    tr = train or Train()
    q = Sequencer(tr.send)
    q.tick(0.0, tr.state())
    q.start(name, mode, **opt)
    while q.active and tr.t < until:
        tr.step()
        q.tick(tr.t, tr.state())
    return tr, q


def first(log, pred):
    return next(t for t, m in log if pred(m))


def is_(t, **kw):
    return lambda m: m["t"] == t and all(m.get(k) == v for k, v in kw.items())


@pytest.mark.parametrize("mode", ["costume", "real"])
def test_crossing_signal_is_15_to_20_s_in_real_timing(mode):
    p = signal_pattern("crossing", mode)
    P = TIMINGS[mode]
    assert [round(d, 3) for _, d in p[:3]] == [P["long"], P["long"], P["short"]]  # – – o
    assert pattern_len(p) == pytest.approx(P["x_total"])  # last long prolonged to x_total
    if mode == "real":
        assert 15 <= pattern_len(p) <= 20  # 49 CFR 222.21(b)


def test_signals_follow_gcor():
    P = TIMINGS["costume"]
    assert signal_pattern("proceed") == [(0.0, P["long"]), (P["long"] + P["gap"], P["long"])]
    assert len(signal_pattern("alarm")) == 8 and all(d == 0.35 for _, d in signal_pattern("alarm"))
    assert [d for _, d in signal_pattern("men")] == [P["long"], P["short"]]
    assert len(signal_pattern("station")) == 1


@pytest.mark.parametrize("mode", ["costume", "real"])
def test_crossing_sequence_timing(mode):
    tr, q = run("crossing", mode)
    P = TIMINGS[mode]
    log = tr.log
    assert first(log, is_("bell", on=True)) == 0.0
    t_horn = first(log, is_("pattern"))
    assert t_horn == pytest.approx(0.5, abs=DT)
    t_off = first(log, is_("bell", on=False))
    # bell until the crossing is occupied (GCOR 5.8.1 #4): 0.5 s after the last long ends
    assert t_off == pytest.approx(0.5 + P["x_total"] + P["bell_after_x"], abs=2 * DT)
    assert q.active is None


@pytest.mark.parametrize("mode", ["costume", "real"])
def test_depart_from_open_doors(mode):
    P = TIMINGS[mode]
    tr, q = run("depart", mode, train=Train(accel=60.0 if mode == "real" else 14.0, doors="open"))
    log = tr.log
    names = [m["t"] for _, m in log]
    # door interlock order, then bell BEFORE moving (GCOR 5.8.1 #1), – – (5.8.2(3)), power
    assert names[:6] == ["doors", "hiss", "bell", "pattern", "throttle", "bell"]
    t_closed = 3.5
    t_hiss = first(log, is_("hiss"))
    assert t_hiss == pytest.approx(t_closed + P["after_seal"], abs=2 * DT)
    t_bell = first(log, is_("bell", on=True))
    assert t_bell == pytest.approx(t_hiss + 1.0, abs=DT)
    t_horn = first(log, is_("pattern"))
    assert t_horn == pytest.approx(t_bell + (3.0 if mode == "real" else 1.5), abs=DT)
    t_power = first(log, is_("throttle"))
    assert t_power == pytest.approx(t_horn + pattern_len(signal_pattern("proceed", mode)) + 0.5, abs=DT)
    assert first(log, is_("throttle"))  # power after the bell: never moving without the bell
    assert dict(log)[t_power]["v"] == P["cruise"]
    # bell off once clear of the platform (clear_m travelled)
    t_off = first(log, is_("bell", on=False))
    assert t_off > t_power and tr.speed > 0
    assert q.active is None


def test_depart_without_horn_and_closed_doors():
    tr, _ = run("depart", depart_horn=False)
    names = [m["t"] for _, m in tr.log]
    assert names == ["hiss", "bell", "throttle", "bell"]


def test_arrive_while_moving():
    tr = Train()
    tr.speed = tr.throttle = 0.7
    tr, q = run("arrive", train=tr)
    log = tr.log
    names = [m["t"] for _, m in log]
    assert names == ["bell", "pattern", "throttle", "stop", "bell", "doors"]
    assert dict(log)[first(log, is_("pattern"))]["seq"] == signal_pattern("station")  # Caltrain: one short
    t_stop = first(log, is_("stop"))
    t_off = first(log, is_("bell", on=False))
    t_doors = first(log, is_("doors"))
    assert t_off > t_stop + 2.0  # standing, then the bell off
    assert t_doors == pytest.approx(t_off + TIMINGS["costume"]["before_open"], abs=DT)
    assert tr.doors == "open" and tr.speed == 0.0
    assert q.active is None


def test_manual_press_takes_the_element_back():
    tr = Train()
    q = Sequencer(tr.send)
    q.tick(0.0, tr.state())
    q.start("crossing")
    for _ in range(4):
        tr.step()
        q.tick(tr.t, tr.state())
    q.manual("bell")  # the operator now owns the bell: the sequence must not turn it off
    while q.active:
        tr.step()
        q.tick(tr.t, tr.state())
    assert [m for _, m in tr.log if m["t"] == "bell"] == [{"t": "bell", "on": True}]


def test_cancel_cuts_horn_and_bell_but_keeps_speed():
    tr = Train()
    tr.speed = tr.throttle = 0.5
    q = Sequencer(tr.send)
    q.tick(0.0, tr.state())
    q.start("crossing")
    for _ in range(30):
        tr.step()
        q.tick(tr.t, tr.state())
    q.cancel()
    assert tr.log[-2:] == [(tr.log[-2][0], {"t": "stopPattern"}), (tr.log[-1][0], {"t": "bell", "on": False})]
    assert tr.throttle == 0.5 and q.active is None


def test_auto_run_loops():
    tr = Train()
    q = Sequencer(tr.send)
    q.tick(0.0, tr.state())
    q.start("auto")
    while tr.t < 400 and q.status()["cycles"] < 2:
        tr.step()
        q.tick(tr.t, tr.state())
    assert q.status()["cycles"] == 2 and q.active == "auto"
    crossings = [m for _, m in tr.log if m["t"] == "pattern" and m["seq"] == signal_pattern("crossing")]
    assert len(crossings) == 4  # two per cycle


def test_progress_and_steps():
    s = steps("depart", "costume", {"speed": 0, "doors": "closed"})
    assert s[0] == {"phase": "Depart"}
    tr = Train()
    q = Sequencer(tr.send)
    q.tick(0.0, tr.state())
    q.start("crossing")
    last = 0
    while q.active:
        st = q.status()
        assert st["progress"] >= last - 1e-9
        last = st["progress"]
        tr.step()
        q.tick(tr.t, tr.state())
    assert last > 0.9
