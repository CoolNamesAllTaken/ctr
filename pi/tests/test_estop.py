"""EMERGENCY STOP with the app's real engine (the v12 sidecar if node is here), two fake USB DACs (four speakers) and a
fake lighting controller: instant stop, sequence cancelled, the alarm on all four speakers in both directions, the
stack lights flashing red, the latch and its reset, the controls it locks, and the board's own failsafe red."""

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest
from fake_board import FakeBoard

import caltreat.__main__ as m
from caltreat.config import board_defaults

FAKE = Path(os.environ["CALTREAT_FAKE_AUDIO"])
UID = "E5E5E5E5E5E5E5E5"
SR = 48000
CARDS = {"a_ext": ("usb-1.2", 0), "a_int": ("usb-1.2", 1), "b_ext": ("usb-1.3", 0), "b_int": ("usb-1.3", 1)}


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def latest(name):
    raws = sorted(FAKE.glob(f"{name}-*.raw"), key=lambda p: p.stat().st_mtime)
    raw = raws[-1]
    start = json.loads(raw.with_suffix(".json").read_text())["start"]
    x = np.frombuffer(raw.read_bytes()[: raw.stat().st_size // 4 * 4], dtype="<i2").reshape(-1, 2) / 32768.0
    return x, start


def band(y, f0, f1):
    s = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    f = np.fft.rfftfreq(len(y), 1 / SR)
    return float(np.sqrt(np.sum(s[(f > f0) & (f < f1)] ** 2)))


def last_secs(sp, secs=1.0):
    name, ch = CARDS[sp]
    x, _ = latest(name)
    return x[-int(secs * SR):, ch]


def alarm_on(sp):
    """The two-tone is on this speaker: both 1 kHz and 800 Hz well above the rest of 600-1200 Hz."""
    y = last_secs(sp)
    hi, lo, other = band(y, 980, 1020), band(y, 785, 815), band(y, 600, 760) + band(y, 1050, 1200)
    return hi > 5 * other and lo > 5 * other and np.max(np.abs(y)) > 0.3


@pytest.fixture(scope="module")
def train():
    m.cfg.data["direction"] = "A"
    m.cfg.data["speakers"] = {sp: {"device": f"usb:{n[4:]}", "ch": "left" if ch == 0 else "right", "level": 100}
                              for sp, (n, ch) in CARDS.items()}
    m.cfg.data["estop"] = {"level": 80, "quiet": 35, "loud_s": 30, "inside": 70}  # loud through these tests (quiet: test_safety)
    m.cfg.data["boards"][UID] = board_defaults("Z")  # Z: not a car board; the stack is mapped below
    outs = m.cfg.data["boards"][UID]["outputs"]
    for pin, fid in {4: "stack_green", 5: "stack_yellow", 6: "stack_red", 7: "stack_blue"}.items():
        outs[pin] = {"fn": fid, "alt": False}
    m.cfg.save()
    if m.sidecar:
        m.sidecar.start()
        assert wait_for(lambda: m.sidecar.ready, 20), m.sidecar.error
    m.router.start()
    fb = FakeBoard("127.0.1.11", int(os.environ["CALTREAT_BOARD_PORT"]), uid=UID).start()
    rb = m.RemoteBoard("Z", UID, "127.0.1.11")
    with m.finder.lock:
        m.finder.boards[UID] = rb
    rb.start()
    m.io.running = False
    m.io.start()
    m.seq_driver.start()
    assert wait_for(lambda: rb.connected, 10)
    yield fb, rb
    m.estop.reset()
    m.seq_driver.stop()
    m.io.stop()
    rb.stop()
    with m.finder.lock:
        m.finder.boards.pop(UID, None)
    fb.stop()
    m.router.stop()
    if m.sidecar:
        m.sidecar.stop()


def stack_coils(fb):
    return fb.coils[4:8]  # green, yellow, red, blue


def test_power_on_red_then_green_when_moving(train):
    fb, _ = train
    assert wait_for(lambda: stack_coils(fb) == [False, False, True, False])
    if m.sidecar:
        m.audio.send({"t": "accel", "v": 2})  # quick, for the test
    m.act("t", "throttle", 60)
    assert wait_for(lambda: stack_coils(fb) == [True, False, False, False], 3)
    assert wait_for(lambda: m.audio.motor.speed > 0.4, 10)


def test_failsafe_levels_written_to_the_board(train):
    fb, _ = train
    flags = [fb.holding.get(2000 + 8 * pin + 3) for pin in range(8)]
    assert flags[:4] == [None] * 4  # not stack lights: the board's own default
    assert [bool(f & 0x10) for f in flags[4:]] == [False, False, True, False]  # red on, the rest off


def test_estop_stops_at_once_and_alarms_everywhere(train):
    fb, _ = train
    assert m.audio.motor.speed > 0.4
    m.act("t", "seq", "crossing")  # a sequence running (works while moving)
    assert m.sequencer.active == "crossing"
    m.act("t", "estop", None)
    assert m.audio.motor.speed == 0.0 and m.audio.motor.throttle == 0.0  # at once
    assert m.sequencer.active is None and m.estop.latched
    st = m.engine_state("t")
    assert st["estop"] and st["mph"] == 0 and st["stack"] == "estop"
    if m.sidecar:  # the engine itself: stopped within a few state reports, and it stays there
        assert wait_for(lambda: m.sidecar.state.get("speed", 1) == 0.0, 1.0)
        assert not m.sidecar.state.get("patternBusy")
    # stack: red flashing, never green / yellow
    seen = set()
    end = time.monotonic() + 1.2
    while time.monotonic() < end:
        seen.add(tuple(stack_coils(fb)))
        time.sleep(0.03)
    assert seen == {(False, False, True, False), (False, False, False, False)}
    time.sleep(1.0)
    for sp in CARDS:  # all four, car A leading
        assert alarm_on(sp), sp
    assert m.audio.motor.speed == 0.0


def test_latched_blocks_power_depart_and_closing_doors(train):
    assert m.estop.latched
    m.act("t", "throttle", 80)
    assert m.audio.motor.throttle == 0.0
    for s in ("depart", "auto", "arrive"):
        m.act("t", "seq", s)
        assert m.sequencer.active is None
    m.act("t", "door", True)  # opening still works
    m.act("t", "door", False)
    assert wait_for(lambda: m.audio.doors in ("opening", "open"), 3)
    assert wait_for(lambda: m.audio.doors == "open", 12)
    m.act("t", "door", True)  # closing is refused
    m.act("t", "door", False)
    time.sleep(0.5)
    assert m.audio.doors == "open"


def test_alarm_on_all_four_with_car_b_leading(train):
    assert m.set_direction("B")  # standing, power off: allowed while latched
    time.sleep(1.2)
    for sp in CARDS:
        assert alarm_on(sp), sp


def test_board_link_lost_board_shows_red_itself(train):
    fb, rb = train
    rb.stop()  # the Pi stops talking (app crash, cable): the board falls back to its failsafe levels
    assert wait_for(lambda: stack_coils(fb) == [False, False, True, False], 3)
    rb.start()
    assert wait_for(lambda: rb.connected, 10)


def test_reset_quiet_then_power_works(train):
    fb, _ = train
    m.act("t", "estop_reset", None)
    assert not m.estop.latched
    time.sleep(1.0)
    for sp in CARDS:
        assert not alarm_on(sp), sp
    assert wait_for(lambda: stack_coils(fb) == [False, False, True, False], 2)  # steady red: doors open
    seen = set()
    for _ in range(20):
        seen.add(tuple(stack_coils(fb)))
        time.sleep(0.03)
    assert seen == {(False, False, True, False)}
    m.act("t", "door", True)
    m.act("t", "door", False)
    assert wait_for(lambda: m.audio.doors == "closed", 15)
    assert m.set_direction("A")
    m.act("t", "throttle", 30)
    assert m.audio.motor.throttle == pytest.approx(0.3)
    assert wait_for(lambda: stack_coils(fb) == [True, False, False, False], 3)
    m.act("t", "throttle", 0)
    assert wait_for(lambda: stack_coils(fb) == [False, True, False, False], 3)  # slowing
    assert wait_for(lambda: stack_coils(fb) == [False, False, True, False], 15)  # stopped
