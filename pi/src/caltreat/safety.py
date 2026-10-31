"""The walkers' stack lights and the EMERGENCY STOP.

People walk inside the costume, and a four-colour stack light on each lighting controller tells them when to walk:

    GREEN   walk forward: power applied and the train speeding up or holding its speed
    YELLOW  slowing down: moving, without power (or with less power than its speed), STOP or Arrive braking
    RED     stopped: standing, at power-on, while the engine is not ready, while the doors are not closed
    RED flashing  EMERGENCY STOP (latched until the engineer resets it)
    BLUE    not assigned yet: a manual light (config.py), not driven from here

`Stack` turns the engine's state into one of those, with hysteresis so the lights never flicker: a change towards
red (more restrictive) shows at once, a change towards green only after the new state has held for `HOLD` s.

`Alarm` is the E-STOP warning tone: a two-tone (1 kHz / 800 Hz, 0.25 s each) at the loud level for `loud_s` s, then
the same two-tone once every `QUIET_EVERY` s at the quieter level while the E-STOP stays latched. The Router adds it
to every speaker output, after the intercom / recordings ducking and whatever the direction.
"""

import numpy as np

GREEN, YELLOW, RED, ESTOP = "green", "yellow", "red", "estop"
RANK = {GREEN: 0, YELLOW: 1, RED: 2, ESTOP: 3}  # higher = more restrictive
COLORS = ("green", "yellow", "red", "blue")
MPH_PER_SPEED = 79
MOVING_MPH = 0.3  # below this the train stands (as the reverser's standstill)
HOLD = 0.5  # s a greener state must hold before it shows
FLASH_HALF = 0.25  # s: E-STOP red flashes at 2 Hz (steady red = stopped)
SLOWING = 0.01  # speed above throttle by more than this (engine units, ~0.8 mph) = decelerating


def raw_state(speed, throttle, stopping=False, doors="closed", ready=True):
    """The stack colour for this instant, without hysteresis. speed / throttle: engine units 0-1."""
    if not ready or doors != "closed":
        return RED
    powered = throttle > 0.01 and not stopping
    if powered and speed <= throttle + SLOWING:  # accelerating towards the throttle or cruising at it
        return GREEN
    if speed * MPH_PER_SPEED >= MOVING_MPH:
        return YELLOW
    return RED


class Stack:
    """The stack lights' state with hysteresis. update() every few tens of ms; `state` is what the lights show."""

    def __init__(self, hold=HOLD):
        self.hold = hold
        self.state = RED  # power-on: stopped
        self.pending = None  # (greener state, since)
        self.changed = 0.0

    def update(self, now, speed, throttle, stopping=False, doors="closed", ready=True, estop=False):
        want = ESTOP if estop else raw_state(speed, throttle, stopping, doors, ready)
        if want == self.state:
            self.pending = None
        elif RANK[want] > RANK[self.state]:  # more restrictive: at once
            self.state, self.pending, self.changed = want, None, now
        else:  # greener: only once it has held
            if self.pending is None or self.pending[0] != want:
                self.pending = (want, now)
            elif now - self.pending[1] >= self.hold:
                self.state, self.pending, self.changed = want, None, now
        return self.state

    def lamps(self, now):
        """{"green", "yellow", "red": on} for this moment (E-STOP: red flashing)."""
        s = self.state
        if s == ESTOP:
            return {GREEN: False, YELLOW: False, RED: int(now / FLASH_HALF) % 2 == 0}
        return {GREEN: s == GREEN, YELLOW: s == YELLOW, RED: s == RED}


class Alarm:
    """The E-STOP tone, rendered per block at the Router's sample rate. start() / stop(); render(n) -> (n,) or None."""

    F = (1000.0, 800.0)  # Hz, alternating
    HALF = 0.25  # s per tone (0.5 s cycle)
    RAMP = 0.004  # s fade at each tone edge (no clicks)
    QUIET_EVERY = 3.0  # s between quiet bursts once the loud part is over
    QUIET_BURST = 1.0  # s: two cycles

    def __init__(self, sr, settings=dict):
        self.sr = sr
        self.settings = settings  # -> {"level": %, "quiet": %, "loud_s": s, "inside": %}
        self.on = False
        self.pos = 0  # samples since start
        self.ph = 0.0
        self.test_until = None  # samples: a test burst (Settings), not latched

    def start(self):
        if not self.on:
            self.on, self.pos, self.test_until = True, 0, None

    def test(self, secs=2.0):
        """Settings: play the loud part for `secs` s (does not latch anything)."""
        if not self.on:
            self.on, self.pos, self.test_until = True, 0, int(secs * self.sr)

    def stop(self):
        self.on, self.test_until = False, None

    def envelope(self, t):
        """Level 0-1 per sample time t (s since start): loud, then quiet bursts."""
        s = self.settings() or {}
        loud, quiet, loud_s = s.get("level", 60) / 100, s.get("quiet", 30) / 100, s.get("loud_s", 5)
        cyc = np.mod(t - loud_s, self.QUIET_EVERY)
        return np.where(t < loud_s, loud, np.where(cyc < self.QUIET_BURST, quiet, 0.0))

    def render(self, n):
        if not self.on:
            return None
        sr = self.sr
        i = self.pos + np.arange(n)
        if self.test_until is not None and i[0] >= self.test_until:
            self.stop()
            return None
        t = i / sr
        k = (t / self.HALF).astype(int) % 2  # which tone
        f = np.where(k == 0, self.F[0], self.F[1])
        ph = self.ph + np.cumsum(f) / sr
        self.ph = float(ph[-1] % 1.0)
        w = 2 * np.pi * ph
        tone = (np.sin(w) + np.sin(3 * w) / 3 + np.sin(5 * w) / 5) * 1.0  # a softened square (peak ~0.9 at 100 %): cuts through noise
        edge = np.mod(t, self.HALF)
        fade = np.clip(np.minimum(edge, self.HALF - edge) / self.RAMP, 0, 1)
        y = tone * fade * self.envelope(t)
        if self.test_until is not None:
            y = y * (i < self.test_until)
        self.pos += n
        return y
