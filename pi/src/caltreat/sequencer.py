"""Depart / Arrive / Crossing / Auto run sequences and the GCOR 5.8.2 horn signals.

A Python port of the v12 browser sequencer (caltrain-sounds `app/sequencer.js`, rules and sources in its
`docs/procedures.md`): GCOR 8th ed. 5.8.1 (bell before moving, bell at crossings), 5.8.2 (horn signals) and
49 CFR 222.21 (crossing signal 15-20 s before the crossing, last long prolonged until it is occupied).

The sequencer has no timers or threads of its own: the owner calls `tick(now, state)` often (the app every 50 ms)
with the engine state {"speed": 0..1, "doors": "closed" | "opening" | "open" | "closing"}, and the sequencer sends
the same messages the buttons send, through `send(msg)`:

    {"t": "doors", "v": "open" | "close"}   {"t": "hiss"}   {"t": "bell", "on": bool}   {"t": "throttle", "v": 0..1}
    {"t": "pattern", "seq": [(start s, duration s), ...]}   {"t": "stopPattern"}   {"t": "stop"} (brake to a halt)

A manual press of an element during a sequence (`manual("horn" | "bell" | "doors" | "power" | "brake" | "air")`)
drops the sequence's later steps for that element for the rest of the run (or Auto run phase): the operator wins.
"""

MPH = 79  # engine speed 1.0 = 79 mph
MS_PER_MPH = 0.44704

# Blast lengths (s) and sequence timings. GCOR defines only "long" and "short"; "real" is what engineers sound,
# "costume" (shown as Show) is shorter for the costume. x_total: the crossing signal from its first sound until the
# lead car occupies the crossing (49 CFR 222.21(b): 15-20 s).
TIMINGS = {
    "real": dict(long=2.5, short=0.8, gap=0.6, x_total=17, cruise=0.7, clear_m=400, coast=4, dwell=40, cruise_s=70,
                 bell_after_x=0.5, after_seal=1.5, before_open=1.5),
    "costume": dict(long=1.6, short=0.5, gap=0.5, x_total=10, cruise=0.6, clear_m=150, coast=1, dwell=12, cruise_s=22,
                    bell_after_x=0.5, after_seal=0.6, before_open=0.6),
}

# GCOR 5.8.2 (8th ed., 2020) signals plus Caltrain's one short approaching a station. o = short, – = long.
# (key, page label, signal, rule for the tooltip)
SIGNALS = [
    ("stop", "Brakes on", "–", "GCOR 5.8.2(2): one long: when stopped, air brakes applied, pressure equalized"),
    ("proceed", "Proceed", "– –", "GCOR 5.8.2(3): two long: release brakes, proceed"),
    ("ack", "Ack", "o o", "GCOR 5.8.2(4): two short: acknowledgment of any signal not otherwise provided for"),
    ("back", "Back up", "o o o", "GCOR 5.8.2(5): three short: when stopped, back up; acknowledge a hand signal to "
                                 "back up"),
    ("request", "Signal?", "o o o o", "GCOR 5.8.2(6): four short: request a signal be given or repeated if not "
                                      "understood"),
    ("men", "Men", "– o", "GCOR 5.8.2(8): long, short: approaching men or equipment on or near the track (then o o "
                          "until passed)"),
    ("station", "Station", "o", "Caltrain practice (2017): one short approaching a station; no rule requires a horn "
                                "at stations (49 CFR 222.23(d))"),
    ("crossing", "Crossing", "– – o –", "49 CFR 222.21 / GCOR 5.8.2(7): long, long, short, long at public grade "
                                        "crossings, 15-20 s before, last long prolonged until the lead car occupies "
                                        "the crossing"),
    ("alarm", "Alarm", "o o o o o o o o", "GCOR 5.8.2(1): succession of short sounds: persons or livestock on the "
                                          "track, or an emergency"),
]
SIGNAL = {k: (label, sig, rule) for k, label, sig, rule in SIGNALS}


def pattern(sig, P, last_long=None):
    """A signal written with o (short) and – (long) -> [(start s, duration s), ...]. A final long can be prolonged."""
    out, t = [], 0.0
    s = "".join(sig.split())
    for i, ch in enumerate(s):
        long = ch in "–-"
        d = (last_long if (i == len(s) - 1 and last_long) else P["long"]) if long else P["short"]
        out.append((round(t, 3), d))
        t += d + P["gap"]
    return out


def pattern_len(p):
    return p[-1][0] + p[-1][1] if p else 0.0


def signal_pattern(name, mode="costume"):
    P = TIMINGS.get(mode, TIMINGS["costume"])
    sig = SIGNAL[name][1]
    if name == "alarm":  # 8 quick shorts (GCOR gives no count; tap again to repeat)
        return pattern(sig, dict(long=P["long"], short=0.35, gap=0.25))
    if name != "crossing":
        return pattern(sig, P)
    head = pattern("– – o", P)  # the last long fills up to x_total
    return pattern(sig, P, P["x_total"] - pattern_len(head) - P["gap"])


# ---------- sequences: lists of steps run in order ----------
# {"label", "el", "act": fn(ctx)} acts at once; {"wait": s} waits; {"until": fn(state, run), "max", "est"} waits for a
# condition (or max s); {"phase": name} starts a phase (manual overrides reset, for Auto run).

def _depart(P, st, opt):
    s = [{"phase": "Depart"}]
    if st.get("doors") in ("open", "opening"):
        s += [{"label": "Doors close", "el": "doors", "act": lambda c: c.send({"t": "doors", "v": "close"})},
              {"label": "Doors closed and locked", "until": lambda q, r: q.get("doors") == "closed", "max": 14,
               "est": 5.2},
              {"wait": P["after_seal"]}]
    real = P is TIMINGS["real"]
    pr = pattern("– –", P)
    s += [{"label": "Brake release (air)", "el": "air", "act": lambda c: c.send({"t": "hiss"})}, {"wait": 1.0},
          {"label": "Bell on (before moving)", "el": "bell", "act": lambda c: c.bell(True)},
          {"wait": 3.0 if real else 1.5}]
    if opt.get("depart_horn", True):
        s += [{"label": "Horn – – (proceed)", "el": "horn", "act": lambda c: c.horn(pr)},
              {"wait": pattern_len(pr) + 0.5}]
    s += [{"label": "Power to cruise", "el": "power", "act": lambda c: c.send({"t": "throttle", "v": P["cruise"]})},
          {"label": "Leaving the platform", "until": lambda q, r: r.dist >= P["clear_m"], "max": 90,
           "est": 37 if real else 11, "mark": True},
          {"label": "Bell off (clear of the platform)", "el": "bell", "act": lambda c: c.bell(False)}]
    return s


def _arrive(P, st, opt=None):
    moving = st.get("speed", 0) > 0.02
    ap = pattern("o", P)
    s = [{"phase": "Arrive"},
         {"label": "Bell on (approaching the station)", "el": "bell", "act": lambda c: c.bell(True)}, {"wait": 0.8}]
    if moving:
        s += [{"label": "Horn o (approaching the station)", "el": "horn", "act": lambda c: c.horn(ap)},
              {"wait": pattern_len(ap) + 0.3},
              {"label": "Power off", "el": "power", "act": lambda c: c.send({"t": "throttle", "v": 0})},
              {"wait": P["coast"]}]
    s += [{"label": "Brake to a stop", "el": "brake", "act": lambda c: c.send({"t": "stop"})},
          {"label": "Stopping", "until": lambda q, r: q.get("speed", 0) < 0.003, "max": 60,
           "est": 12 if moving else 0.5},
          {"wait": 2.0 if moving else 4.5},
          {"label": "Bell off (stopped)", "el": "bell", "act": lambda c: c.bell(False)}, {"wait": P["before_open"]},
          {"label": "Doors open", "el": "doors", "act": lambda c: c.send({"t": "doors", "v": "open"})},
          {"label": "Doors open", "until": lambda q, r: q.get("doors") == "open", "max": 10, "est": 6.2}]
    return s


def _crossing(P, st=None, opt=None):
    x = signal_pattern("crossing", "real" if P is TIMINGS["real"] else "costume")
    return [{"phase": "Crossing"},
            {"label": "Bell on (approaching the crossing)", "el": "bell", "act": lambda c: c.bell(True)},
            {"wait": 0.5},
            {"label": "Horn – – o – (crossing)", "el": "horn", "act": lambda c: c.horn(x)}, {"wait": pattern_len(x)},
            {"label": "Crossing occupied", "wait": P["bell_after_x"]},
            {"label": "Bell off", "el": "bell", "act": lambda c: c.bell(False)}]


def _auto(P, st, opt):
    """Depart -> cruise with two crossings -> Arrive -> dwell (start() repeats it until cancelled)."""
    x = _crossing(P)
    xl = sum(s.get("wait", 0) for s in x)
    gap = max(2, (P["cruise_s"] - 2 * xl) / 3)
    return [*_depart(P, st, opt), {"phase": "Cruise"}, {"label": "Cruising", "wait": gap}, *x[1:],
            {"label": "Cruising", "wait": gap}, *x[1:], {"label": "Cruising", "wait": gap},
            *_arrive(P, {"speed": 1, "doors": "closed"}), {"phase": "Dwell"},
            {"label": "Dwell (passengers)", "wait": P["dwell"]}]


BUILDERS = {"depart": _depart, "arrive": _arrive, "crossing": _crossing, "auto": _auto}


def steps(name, mode="costume", st=None, opt=None):
    return BUILDERS[name](TIMINGS.get(mode, TIMINGS["costume"]), st or {"speed": 0, "doors": "closed"}, opt or {})


def _est(s):
    if "wait" in s:
        return s["wait"]
    return s["est"] if "until" in s else 0


class _Run:
    def __init__(self, name, mode, st, opt, now):
        self.name, self.mode, self.P, self.opt = name, mode, TIMINGS[mode], opt
        self.loop = name == "auto"
        self.cycles = 0
        self.over = {}
        self.bell_by = False
        self.horn = 0.0  # time the sequence's horn pattern ends
        self.dist = 0.0  # m travelled since the last "mark" step
        self.phase = None
        self.mark_i = None
        self.reset(st, now)

    def reset(self, st, now):
        self.steps = BUILDERS[self.name](self.P, st, self.opt)
        self.i = 0
        self.t0 = self.tw = now
        self.done = 0.0
        self.total = sum(_est(s) for s in self.steps)


class _Ctx:
    def __init__(self, q, r):
        self.q, self.r = q, r

    def send(self, m):
        self.q.out(m)

    def bell(self, on):
        if self.r.over.get("bell"):
            return
        self.q.out({"t": "bell", "on": on})
        self.r.bell_by = on

    def horn(self, p):
        if self.r.over.get("horn"):
            return
        self.q.out({"t": "pattern", "seq": p})
        self.r.horn = self.q.t + pattern_len(p)


class Sequencer:
    def __init__(self, send=None):
        self.out = send or (lambda m: None)
        self.run = None
        self.t = 0.0
        self.st = {"speed": 0.0, "doors": "closed"}

    @property
    def active(self):
        return self.run.name if self.run else None

    def start(self, name, mode="costume", **opt):
        if name not in BUILDERS:
            raise ValueError(f"no sequence {name}")
        if self.run:
            self.cancel()
        self.run = _Run(name, mode if mode in TIMINGS else "costume", dict(self.st), opt, self.t)
        self._advance()

    def cancel(self):
        """The sequence's own bell goes off and its horn pattern is cut; the train keeps its speed."""
        r = self.run
        if not r:
            return
        self.run = None
        if r.horn > self.t and not r.over.get("horn"):
            self.out({"t": "stopPattern"})
        if r.bell_by and not r.over.get("bell"):
            self.out({"t": "bell", "on": False})

    def manual(self, el):
        """A manual press of an element during a sequence: it is the operator's for the rest of the run (phase)."""
        r = self.run
        if not r:
            return
        r.over[el] = True
        if el == "power":
            r.over["brake"] = True
        if el == "brake":
            r.over["power"] = True
        if el == "horn":
            r.horn = 0.0
        if el == "bell":
            r.bell_by = False

    def signal(self, name, mode="costume"):
        """Sound a horn signal (also a manual horn press for a running sequence). Returns its length in s."""
        p = signal_pattern(name, mode)
        self.manual("horn")
        self.out({"t": "pattern", "seq": p, "key": name})
        return pattern_len(p)

    def tick(self, now, st=None):
        if st:
            self.st = st
        dt = max(0.0, now - self.t)
        self.t = now
        r = self.run
        if not r:
            return
        r.dist += (self.st.get("speed", 0) or 0) * MPH * MS_PER_MPH * dt
        self._advance()

    def _advance(self):
        r = self.run
        c = _Ctx(self, r)
        for _ in range(200):
            if self.run is not r:
                return
            if r.i >= len(r.steps):
                if r.loop:
                    r.cycles += 1
                    r.reset(dict(self.st), self.t)
                    continue
                self.run = None
                return
            s = r.steps[r.i]
            if "phase" in s:
                r.over, r.phase = {}, s["phase"]
                r.i += 1
            elif "act" in s:
                if not r.over.get(s["el"]):
                    s["act"](c)
                r.i += 1
            elif "wait" in s:
                if self.t - r.tw + 1e-9 < s["wait"]:
                    return
                r.done += s["wait"]
                r.tw += s["wait"]
                r.i += 1
            elif "until" in s:
                if s.get("mark") and r.mark_i != r.i:
                    r.mark_i, r.dist = r.i, 0.0
                if not s["until"](self.st, r) and self.t - r.tw < s["max"]:
                    return
                r.done += s["est"]
                r.i += 1
                r.tw = self.t
            else:
                r.i += 1

    def status(self):
        r = self.run
        if not r:
            return {"active": None, "progress": 0.0, "step": "", "phase": None, "mode": None}
        label = ""
        for i in range(min(r.i, len(r.steps) - 1), -1, -1):
            if r.steps[i].get("label"):
                label = r.steps[i]["label"]
                break
        s = r.steps[r.i] if r.i < len(r.steps) else None
        in_step = min(_est(s), self.t - r.tw) if s else 0.0
        return {"active": r.name, "mode": r.mode, "phase": r.phase, "step": label, "index": r.i,
                "steps": len(r.steps), "progress": min(1.0, (r.done + max(0.0, in_step)) / max(1e-6, r.total)),
                "over": sorted(r.over), "cycles": r.cycles}
