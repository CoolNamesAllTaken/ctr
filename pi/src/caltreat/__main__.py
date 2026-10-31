# """Caltreat: a NiceGUI page that sets the Pi's 3.5 mm jack volume and plays Caltrain KISS EMU cab sounds.

# The voices are numpy ports of the sound engine in publish.html: horn (one harmonic series near F#4 with its
# measured harmonic levels, onset pitch overshoot, one-pole attack/release and faint air noise), the electronic
# bell, the door-closing tone, the traction inverter whine, the air-release hiss and the brake squeal, plus the
# horn signal patterns. Audio is synthesized in blocks and piped as raw PCM into `aplay` on the headphone card, so
# no PortAudio or other system packages are needed; volume is the card's ALSA `PCM` control, set with `amixer`.
# All browsers share the one speaker, so the engine state (bell, doors, throttle, ...) is shared too.

# The page also switches the lights on a 4-channel relay shield (white head/ditch lights, red marker lights).
# """

# import fcntl
# import re
# import subprocess
# import sys
# import threading
# import time

# import numpy as np
# from nicegui import app, ui

# SR = 48000
# BLOCK = 512  # ~11 ms per synthesized block
# ALSA_CARD = "Headphones"
# ALSA_DEVICE = f"plughw:CARD={ALSA_CARD}"
# MIXER_CONTROL = "PCM"
# USB_MIXER_CONTROL = "Speaker"  # the C-Media USB cards' playback volume
# MASTER = 1.1 * 0.85  # publish.html's default master volume, before its soft knee
# # publish.html's default mix levels
# HORN_INTENSITY = 0.85
# BELL_LEVEL = 0.75
# MOTOR_LEVEL = 0.8
# DOOR_LEVEL = 0.7
# HISS_LEVEL = 0.7
# ACCEL_TIME = 14.0  # s, standstill to full ("Costume")
# LOOP = 1 << 18  # ~5.5 s noise loops

# # Fitted to recordings of Caltrain Stadler KISS EMUs (publish.html KISS_PARAMS).
# HORN = dict(
#     f0=376.2,
#     harm=[-0.4, 0.0, -5.6, -8.1, -13.2, -12.9, -16.5, -23.4, -23.9, -22.3, -23.5, -24.5],
#     bend=56.7,
#     bend_tau=0.15,
#     atk=0.061,
#     rel=0.293,
#     noise=-55.9,
# )
# BELL = dict(
#     period=0.4,
#     partials=[[805.0, -10.6, -37.6], [1387.5, 0.0, -4.1], [2035.0, -6.2, -23.8], [2737.5, -18.1, -24.0],
#               [3495.0, -16.0, -29.9], [4312.5, -27.0, -65.2], [5202.5, -9.2, -147.9]],  # Hz, dB, dB/s
# )
# DOOR = dict(f0=1760, harm=[-0.8, 0.0, -18.9, -24.0])
# MOTOR = dict(a_max=980, c_slope=2.144, c_off=34.3, b_slope=2.985, b_off=745.2, d_slope=0.181, carrier=2022,
#              car_slope=0.419, lv_c=-7.4, lv_car=-2.8, lv_b=-12, lv_d=-8)
# HISS = dict(whistle=7214, dur=3.0)
# SQUEAL = dict(f=2150, wobble=125, dur=3.5)

# # (name, signal, [(start s, duration s), ...]); – long, o short
# PATTERNS = [
#     ("Crossing", "– – o –", [(0, 2.0), (2.6, 2.0), (5.2, 0.7), (6.4, 3.5)]),
#     ("Proceed", "– –", [(0, 1.6), (2.1, 1.6)]),
#     ("Acknowledge", "o o", [(0, 0.5), (0.9, 0.5)]),
#     ("Approaching", "–", [(0, 2.5)]),
#     ("Backing up", "o o o", [(0, 0.5), (0.9, 0.5), (1.8, 0.5)]),
#     ("Horn show", "o o – o –", [(0, 0.35), (0.6, 0.35), (1.2, 1.4), (3.0, 0.35), (3.6, 2.2)]),
# ]


# def db(d):
#     return 10.0 ** (np.asarray(d) / 20.0)


# def tau_coef(tau):
#     return 1.0 - np.exp(-1.0 / (SR * tau))


# def ramp(x, a, b):
#     """0 below a, 1 above b."""
#     return min(max((x - a) / (b - a), 0.0), 1.0)


# def biquad(kind, f, q, gain_db=0.0):
#     """RBJ cookbook coefficients (b, a) with a[0] == 1, as the JS `Biquad`."""
#     w = 2 * np.pi * min(f, SR * 0.45) / SR
#     c, al, A = np.cos(w), np.sin(w) / (2 * q), 10 ** (gain_db / 40)
#     if kind == "lp":
#         b, a = [(1 - c) / 2, 1 - c, (1 - c) / 2], [1 + al, -2 * c, 1 - al]
#     elif kind == "hp":
#         b, a = [(1 + c) / 2, -(1 + c), (1 + c) / 2], [1 + al, -2 * c, 1 - al]
#     elif kind == "bp":
#         b, a = [al, 0.0, -al], [1 + al, -2 * c, 1 - al]
#     else:  # peak
#         b, a = [1 + al * A, -2 * c, 1 - al * A], [1 + al / A, -2 * c, 1 - al / A]
#     return [x / a[0] for x in b], [x / a[0] for x in a]


# def one_pole(k):
#     """y += (x - y) * k"""
#     return [k], [1.0, -(1 - k)]


# def filtered_noise_loop(rng, n, *sections):
#     """Uniform noise in [-1, 1) through the cascaded IIR sections (b, a), shaped in the frequency domain so the
#     buffer loops seamlessly. Stands in for the per-sample noise filters of the JS engine."""
#     spectrum = np.fft.rfft(rng.uniform(-1.0, 1.0, n))
#     z = np.exp(-1j * 2 * np.pi * np.arange(len(spectrum)) / n)
#     for b, a in sections:
#         spectrum *= np.polyval(b[::-1], z) / np.polyval(a[::-1], z)
#     return np.fft.irfft(spectrum, n)


# def one_pole_env(env, tgt, pow_n):
#     """Per-sample one-pole glide from env towards tgt over a block, given (1 - coef) ** [1..BLOCK]."""
#     return tgt + (env - tgt) * pow_n


# class Horn:
#     """Block-rendered port of the JS `Horn`; the gate is sampled once per block."""

#     def __init__(self, intensity=HORN_INTENSITY, seed=12345):
#         P = HORN
#         rng = np.random.default_rng(seed)
#         tilt = (1 - intensity) * 1.6  # louder = brighter: tilt the upper harmonics
#         self.hk = db([d - tilt * i for i, d in enumerate(P["harm"])])[:, None]
#         self.k = np.arange(1, len(P["harm"]) + 1)[:, None]
#         self.norm = 1.5 / self.hk.sum()
#         self.level = 0.15 + 0.85 * intensity
#         self.bend_depth = P["bend"] * (0.6 + 0.4 * intensity)
#         n = np.arange(1, BLOCK + 1)
#         self.atk_pow = (1 - tau_coef(P["atk"] / 2.197)) ** n
#         self.rel_pow = (1 - tau_coef(P["rel"] / 2.197)) ** n
#         self.t = np.arange(BLOCK) / SR
#         self.jitter = filtered_noise_loop(rng, LOOP, one_pole(tau_coef(0.08)))
#         self.breath = filtered_noise_loop(rng, LOOP, biquad("bp", 2500, 0.6))
#         self.pos = 0
#         self.env = 0.0
#         self.gate = False
#         self.phase = 0.0
#         self.bend_t = 1e9

#     def set_gate(self, on):
#         if on and not self.gate and self.env < 0.35:
#             self.bend_t = 0.0
#         self.gate = on

#     def render(self, t0):
#         if not self.gate and self.env == 0.0:
#             return None
#         P = HORN
#         tgt = 1.0 if self.gate else 0.0
#         env = one_pole_env(self.env, tgt, self.atk_pow if tgt > self.env else self.rel_pow)
#         self.env = 0.0 if not self.gate and env[-1] < 1e-5 else float(env[-1])

#         idx = (self.pos + np.arange(BLOCK)) % LOOP
#         self.pos = (self.pos + BLOCK) % LOOP
#         bt = self.bend_t + self.t
#         self.bend_t += BLOCK / SR
#         bend = self.bend_depth * np.exp(-bt / P["bend_tau"])
#         f = P["f0"] * 2.0 ** ((bend + self.jitter[idx] * 8) / 1200)
#         ph = self.phase + np.cumsum(f / SR)
#         self.phase = float(ph[-1] % 1.0)

#         tone = (self.hk * np.sin(2 * np.pi * self.k * ph)).sum(axis=0)
#         breath = self.breath[idx] * (db(P["noise"] + 12) + 0.03 * np.exp(-bt / 0.05))
#         return (tone * self.norm + breath) * env * self.level


# class Bell:
#     """Port of the JS `Bell`: identical strikes every `period`, each a bank of decaying partials plus a short
#     5-9 kHz click; a new strike fades the previous one out in a few ms. Strikes are sample-accurate."""

#     def __init__(self, rng, level=BELL_LEVEL):
#         f, lv, dec = np.array(BELL["partials"]).T
#         self.period = round(BELL["period"] * SR)
#         self.w = (2 * np.pi * f / SR)[:, None]
#         self.amp = db(lv)[:, None]
#         self.log_decay = (dec / 20 / SR * np.log(10))[:, None]  # per-sample ln of the amplitude decay
#         self.norm = 0.8 / db(lv).sum()
#         self.level = level
#         self.click = filtered_noise_loop(rng, LOOP, biquad("bp", 6500, 1.2))
#         self.click_len = int(np.log(1e4) * 0.004 * SR)  # samples until the click is below -80 dB
#         self.fade_fast = np.exp(-1 / (SR * 0.0025))
#         self.fade_release = np.exp(-1 / (SR * 0.12))
#         self.on = False
#         self.next = None  # absolute sample of the next strike; None = at the start of the next block
#         self.strikes = []  # [start, fade_at or None, per-sample fade multiplier]

#     def set_on(self, on):
#         if on and not self.on:
#             self.next = None
#         if not on and self.on and self.next is not None and self.strikes and self.strikes[-1][1] is None:
#             self.strikes[-1][1:] = [self.next, self.fade_release]  # ring to the end of this period, then release
#         self.on = on

#     def _strike(self, at):
#         for s in self.strikes:
#             if s[1] is None or s[1] > at:
#                 s[1:] = [at, self.fade_fast]
#         self.strikes.append([at, None, 1.0])

#     def render(self, t0):
#         if self.on:
#             if self.next is None:
#                 self.next = t0
#             while self.next < t0 + BLOCK:
#                 self._strike(self.next)
#                 self.next += self.period
#         if not self.strikes:
#             return None
#         idx = t0 + np.arange(BLOCK)
#         y = np.zeros(BLOCK)
#         for s in list(self.strikes):
#             start, fade_at, fm = s
#             k = idx - start
#             live = k >= 0
#             k = np.maximum(k, 0)
#             v = (np.sin(self.w * (k + 1)) * self.amp * np.exp(self.log_decay * k)).sum(axis=0)
#             if k[0] < self.click_len:
#                 v += self.click[idx % LOOP] * np.exp(-k / (SR * 0.004)) * 0.9
#             v *= np.minimum(k / 48, 1.0) * live
#             if fade_at is not None:
#                 fade = fm ** np.maximum(idx - fade_at + 1, 0)
#                 v *= fade
#                 if fade[-1] < 1e-4:
#                     self.strikes.remove(s)
#             y += v
#         return y * self.norm * self.level


# class Door:
#     """Door-closing warning: continuous A6 (1760 Hz) with a strong 2nd harmonic, gated."""

#     def __init__(self, level=DOOR_LEVEL):
#         self.h = db(DOOR["harm"])[:, None]
#         self.k = np.arange(1, len(DOOR["harm"]) + 1)[:, None]
#         self.norm = 0.5 / self.h.sum()
#         self.w = 2 * np.pi * DOOR["f0"] / SR
#         self.pow = (1 - tau_coef(0.004)) ** np.arange(1, BLOCK + 1)
#         self.level = level
#         self.gate = False
#         self.env = 0.0
#         self.phase = 0.0

#     def render(self, t0):
#         if not self.gate and self.env == 0.0:
#             return None
#         env = one_pole_env(self.env, 1.0 if self.gate else 0.0, self.pow)
#         self.env = 0.0 if not self.gate and env[-1] < 1e-5 else float(env[-1])
#         ph = self.phase + self.w * np.arange(1, BLOCK + 1)
#         self.phase = float(ph[-1] % (2 * np.pi))
#         return (self.h * np.sin(self.k * ph)).sum(axis=0) * self.norm * env * self.level


# class Motor:
#     """Traction inverter / motor whine. `throttle` sets the target speed; speed follows at a limited rate. Line A
#     (0-980 Hz) is the speed proxy; the other lines are fitted functions of A. The JS engine's 64-sample control
#     step runs once per block here, with frequencies and levels interpolated across the block."""

#     STEPS = BLOCK / 64  # JS control steps per block

#     def __init__(self, rng, level=MOTOR_LEVEL):
#         self.level = level
#         self.throttle = 0.0
#         self.speed = 0.0
#         self.load = 0.0
#         self.gain = 0.0
#         self.accel_time = ACCEL_TIME
#         self.coast_time = 30.0
#         self.f = np.zeros(9)
#         self.a = np.zeros(9)
#         self.ph = np.zeros(9)
#         self.rumble = filtered_noise_loop(rng, LOOP, biquad("lp", 220, 0.7))
#         self.r = (np.arange(1, BLOCK + 1) / BLOCK)

#     @staticmethod
#     def k(per_step):
#         return 1 - (1 - per_step) ** Motor.STEPS

#     def control(self):
#         dt, P = BLOCK / SR, MOTOR
#         if self.throttle > self.speed:
#             self.speed = min(self.throttle, self.speed + dt / self.accel_time)
#         else:
#             self.speed = max(self.throttle, self.speed - dt / self.coast_time)
#         if self.throttle > 0.01:
#             motoring = min(max(0.35 + (self.throttle - self.speed) * 3 + 0.3 * self.throttle, 0.0), 1.0)
#         else:
#             motoring = 0.18 * ramp(self.speed, 0, 0.05)
#         self.load += (motoring - self.load) * self.k(0.02)
#         A = P["a_max"] * self.speed
#         C = P["c_slope"] * A + P["c_off"]
#         car = P["carrier"] + P["car_slope"] * A
#         fan = 0.2 * C * ramp(A, 20, 80)
#         idle = 1.0 if self.throttle > 0.01 or self.speed > 0.002 else 0.0
#         lo = 1 - ramp(A, 500, 600)
#         self.f = np.array([A, C, car, car + fan, car - fan, car + 2 * fan, car - 2 * fan,
#                            P["b_slope"] * A + P["b_off"], P["d_slope"] * A])
#         target = np.array([
#             ramp(A, 60, 300),
#             db(P["lv_c"]) * ramp(A, 15, 60) * (1 - ramp(A, 550, 650)),
#             db(P["lv_car"]) * lo * idle,
#             db(P["lv_car"] - 6) * ramp(fan, 5, 40) * lo,
#             db(P["lv_car"] - 6) * ramp(fan, 5, 40) * lo,
#             db(P["lv_car"] - 30) * ramp(fan, 10, 60) * lo,
#             db(P["lv_car"] - 30) * ramp(fan, 10, 60) * lo,
#             db(P["lv_b"]) * ramp(A, 500, 600),
#             db(P["lv_d"]) * ramp(A, 20, 120),
#         ])
#         self.a = self.a + (target - self.a) * self.k(0.05)
#         self.gain += (self.load - self.gain) * self.k(0.05)

#     def render(self, t0):
#         f0, a0, g0 = self.f, self.a, self.gain
#         self.control()
#         if self.gain < 1e-4 and g0 < 1e-4 and self.speed < 1e-4:
#             return None
#         f = f0[:, None] + (self.f - f0)[:, None] * self.r
#         a = a0[:, None] + (self.a - a0)[:, None] * self.r
#         ph = self.ph[:, None] + np.cumsum(f / SR, axis=1)
#         self.ph = ph[:, -1] % 1.0
#         live = (np.maximum(a0, self.a) >= 1e-5) & (self.f > 0) & (self.f < SR * 0.45)
#         y = (a[live] * np.sin(2 * np.pi * ph[live])).sum(axis=0)
#         g = g0 + (self.gain - g0) * self.r
#         rumble = self.rumble[(t0 + np.arange(BLOCK)) % LOOP] * 0.12 * self.speed
#         return (y * 0.22 * g + rumble) * self.level


# class OneShot:
#     """Base for triggered sounds: `start` is the absolute sample of the last trigger, or None when silent."""

#     def __init__(self):
#         self.start = None
#         self.pending = False

#     def trigger(self):
#         self.pending = True

#     def times(self, t0):
#         if self.pending:
#             self.start, self.pending = t0, False
#         if self.start is None:
#             return None
#         return (t0 + np.arange(BLOCK) - self.start) / SR


# class Hiss(OneShot):
#     """Air release: broadband hiss (strongest 2.5-4 kHz) with a faint 7.2 kHz whistle; a one-shot of `dur` s."""

#     def __init__(self, rng, level=HISS_LEVEL):
#         super().__init__()
#         self.level = level
#         self.noise = filtered_noise_loop(rng, LOOP, biquad("hp", 1800, 0.7), biquad("peak", 3200, 0.8, 8),
#                                          biquad("lp", 12000, 0.7))

#     def render(self, t0):
#         t = self.times(t0)
#         if t is None:
#             return None
#         d = HISS["dur"]
#         if t[0] > d + 0.8:
#             self.start = None
#             return None
#         env = np.minimum(1, t / 0.08) * np.where(t < d, 1 - 0.25 * t / d, 0.75 * np.exp(-(t - d) / 0.18))
#         n = self.noise[(t0 + np.arange(BLOCK)) % LOOP]
#         whistle = np.sin(2 * np.pi * HISS["whistle"] * (t0 + np.arange(BLOCK)) / SR)
#         return (n * 0.35 + 0.02 * whistle) * env * self.level


# class Squeal(OneShot):
#     """Brake squeal: a wobbling ~2150 Hz tone (smoothed random frequency walk) with a weaker upper partial."""

#     def __init__(self, rng, level=HISS_LEVEL * 0.7):
#         super().__init__()
#         self.level = level
#         self.wobble = filtered_noise_loop(rng, LOOP, ([0.6 * 0.0006], [1.0, -(1 - 0.0006)]), one_pole(0.0015))
#         self.ph = 0.0
#         self.ph2 = 0.0

#     def render(self, t0):
#         t = self.times(t0)
#         if t is None:
#             return None
#         P = SQUEAL
#         d = P["dur"]
#         if t[0] > d:
#             self.start = None
#             return None
#         f = P["f"] + self.wobble[(t0 + np.arange(BLOCK)) % LOOP] * P["wobble"] * 4
#         ph = self.ph + np.cumsum(f / SR)
#         ph2 = self.ph2 + np.cumsum(1.44 * f / SR)
#         self.ph, self.ph2 = float(ph[-1] % 1.0), float(ph2[-1] % 1.0)
#         env = np.clip(np.minimum(t / 0.3, (d - t) / 0.4), 0, 1) * (0.7 + 0.3 * np.sin(2 * np.pi * 1.7 * t))
#         return (np.sin(2 * np.pi * ph) + 0.25 * np.sin(2 * np.pi * ph2)) * 0.4 * env * self.level


# def soft_knee(v):
#     """Linear below 0.6, tanh knee above (as the JS engine's output stage)."""
#     av = np.abs(v)
#     return np.where(av < 0.6, v, np.sign(v) * (0.6 + 0.4 * np.tanh((av - 0.6) / 0.4)))


# class AudioOut:
#     """Mixes all voices and streams them to `aplay` from a background thread, restarting it if the device goes
#     away. Controls may be called from any thread."""

#     DOOR_TAP = 0.35  # s; a shorter press sounds the door tone for DOOR_TAP_LEN
#     DOOR_TAP_LEN = 3.0

#     def __init__(self):
#         rng = np.random.default_rng(54321)
#         self.horn = Horn()
#         self.bell = Bell(rng)
#         self.door = Door()
#         self.motor = Motor(rng)
#         self.hiss = Hiss(rng)
#         self.squeal = Squeal(rng)
#         self.voices = [self.horn, self.bell, self.door, self.motor, self.hiss, self.squeal]
#         self.lock = threading.Lock()
#         self.t = 0  # absolute sample clock
#         self.holders = set()  # clients holding the horn
#         self.door_holders = {}  # client -> press time
#         self.door_until = 0.0
#         self.pattern = []  # [(start, end) absolute samples]
#         self.pattern_idx = None
#         self.running = False
#         self.error = None
#         self.thread = None

#     # ---------- controls ----------
#     def hold(self, who, on):
#         with self.lock:
#             if on:
#                 self.pattern = []  # holding the horn cancels a running signal
#                 self.holders.add(who)
#             else:
#                 self.holders.discard(who)

#     def toggle_bell(self):
#         with self.lock:
#             self.bell.set_on(not self.bell.on)

#     def door_down(self, who):
#         with self.lock:
#             self.door_until = 0.0
#             self.door_holders[who] = time.monotonic()

#     def door_up(self, who):
#         with self.lock:
#             t0 = self.door_holders.pop(who, None)
#             if t0 is not None and time.monotonic() - t0 < self.DOOR_TAP:
#                 self.door_until = t0 + self.DOOR_TAP_LEN

#     def air(self):
#         self.hiss.trigger()

#     def brakes(self):
#         self.squeal.trigger()

#     def set_throttle(self, v):
#         self.motor.throttle = min(max(float(v), 0.0), 1.0)

#     def play_pattern(self, i):
#         with self.lock:
#             self.pattern = [(self.t + round(s * SR), self.t + round((s + d) * SR)) for s, d in PATTERNS[i][2]]
#             self.pattern_idx = i

#     def release(self, who):
#         """Never leave the horn or door tone stuck on when a client goes away."""
#         with self.lock:
#             self.holders.discard(who)
#             self.door_holders.pop(who, None)

#     # ---------- state for the UI ----------
#     @property
#     def door_on(self):
#         return bool(self.door_holders) or time.monotonic() < self.door_until

#     @property
#     def horn_on(self):
#         return self.horn.gate

#     @property
#     def busy_pattern(self):
#         return self.pattern_idx if self.pattern else None

#     # ---------- audio thread ----------
#     def start(self):
#         self.running = True
#         self.thread = threading.Thread(target=self._run, name="caltreat-audio", daemon=True)
#         self.thread.start()

#     def stop(self):
#         self.running = False
#         if self.thread:
#             self.thread.join(timeout=2)

#     def render(self):
#         with self.lock:
#             t0 = self.t
#             while self.pattern and t0 >= self.pattern[0][1]:
#                 self.pattern.pop(0)
#             pat = bool(self.pattern) and t0 >= self.pattern[0][0]
#             self.horn.set_gate(bool(self.holders) or pat)
#             self.door.gate = self.door_on
#             out = np.zeros(BLOCK)
#             for v in self.voices:
#                 y = v.render(t0)
#                 if y is not None:
#                     out += y
#             self.t += BLOCK
#         return soft_knee(out * MASTER)

#     def _run(self):
#         while self.running:
#             proc = subprocess.Popen(
#                 ["aplay", "-q", "-D", ALSA_DEVICE, "-t", "raw", "-f", "S16_LE", "-r", str(SR), "-c", "1",
#                  "--buffer-time=60000", "--period-time=15000"],
#                 stdin=subprocess.PIPE,
#                 stderr=subprocess.PIPE,
#             )
#             try:
#                 fcntl.fcntl(proc.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)  # keep the pipe from adding latency
#             except OSError:
#                 pass
#             try:
#                 while self.running:
#                     proc.stdin.write((self.render() * 32767).astype("<i2").tobytes())
#                     self.error = None
#             except (BrokenPipeError, OSError):
#                 pass
#             finally:
#                 try:
#                     proc.stdin.close()
#                 except OSError:
#                     pass
#                 proc.terminate()
#                 err = proc.stderr.read().decode(errors="replace").strip()
#                 proc.wait()
#             if self.running:
#                 self.error = err or "aplay exited"
#                 print(f"caltreat: audio output failed, retrying: {self.error}", file=sys.stderr)
#                 time.sleep(2)


# def get_volume(card=ALSA_CARD, control=MIXER_CONTROL):
#     out = subprocess.run(["amixer", "-c", card, "-M", "sget", control],
#                          capture_output=True, text=True).stdout
#     m = re.search(r"\[(\d+)%\]", out)
#     return int(m.group(1)) if m else 50


# def set_volume(pct, card=ALSA_CARD, control=MIXER_CONTROL):
#     subprocess.run(["amixer", "-c", card, "-M", "-q", "sset", control, f"{int(pct)}%"])


# class Lights:
#     """The Inland (Keyestudio KS0212-style) 4-channel relay shield: white head/ditch lights on channels 1-2, red
#     marker lights on 3-4. Each pair is OFF, ON or FLASH (alternating left/right, as ditch lights and crossing
#     flashers do); with `horn_flash` the white pair also alternates while the horn sounds and for a few seconds
#     after, as US ditch lights do at crossings. Relays are driven with `pinctrl`, so no GPIO libraries are needed
#     (the user must be in the `gpio` group)."""

#     PINS = (4, 22, 6, 26)  # BCM GPIO for relay channels 1-4 (J2-J5 on the shield)
#     ACTIVE_HIGH = True  # the shield energizes a relay when its GPIO is high
#     FLASH_HALF = 0.5  # s per side: 60 flashes/min each
#     HORN_HOLD = 5.0  # s the ditch lights keep flashing after the horn stops
#     MODES = ("off", "on", "flash")

#     def __init__(self, audio):
#         self.audio = audio
#         self.white = "off"
#         self.red = "off"
#         self.horn_flash = True
#         self.state = [False] * 4  # relay channels as last written
#         self.last_horn = -1e9
#         self.error = None
#         self.running = False
#         self.thread = None

#     def cycle(self, pair):
#         setattr(self, pair, self.MODES[(self.MODES.index(getattr(self, pair)) + 1) % len(self.MODES)])

#     def desired(self, now):
#         if self.audio.horn_on:
#             self.last_horn = now
#         phase = int(now / self.FLASH_HALF) % 2 == 0
#         white = self.white
#         if white == "on" and self.horn_flash and now - self.last_horn < self.HORN_HOLD:
#             white = "flash"

#         def pair(mode):
#             return {"off": [False, False], "on": [True, True], "flash": [phase, not phase]}[mode]

#         return pair(white) + pair(self.red)

#     def _pinctrl(self, pins, *opts):
#         r = subprocess.run(["pinctrl", "set", ",".join(map(str, pins)), *opts], capture_output=True, text=True)
#         if r.returncode:
#             raise OSError(r.stderr.strip() or f"pinctrl exited with {r.returncode}")

#     def _write(self, want, force=False):
#         changed = [(p, w) for p, w, s in zip(self.PINS, want, self.state) if force or w != s]
#         for level in (True, False):
#             pins = [p for p, w in changed if w == level]
#             if pins:
#                 self._pinctrl(pins, "op", "dh" if level == self.ACTIVE_HIGH else "dl")
#         self.state = list(want)

#     def start(self):
#         self.running = True
#         self.thread = threading.Thread(target=self._run, name="caltreat-lights", daemon=True)
#         self.thread.start()

#     def stop(self):
#         self.running = False
#         if self.thread:
#             self.thread.join(timeout=2)
#         try:
#             self._write([False] * 4, force=True)
#         except OSError:
#             pass

#     def _run(self):
#         force = True
#         while self.running:
#             try:
#                 self._write(self.desired(time.monotonic()), force)
#                 force, self.error = False, None
#             except OSError as e:
#                 if self.error is None:
#                     print(f"caltreat: relay output failed: {e}", file=sys.stderr)
#                 self.error, force = str(e), True
#                 time.sleep(2)
#             time.sleep(0.05)


# def usb_outputs():
#     """[(ALSA card id, USB port path)] for the USB sound cards, ordered by port. Card ids (Device, Device_1, ...)
#     follow enumeration order and can swap between boots; the port path is fixed by where a card is plugged in."""
#     try:
#         cards = open("/proc/asound/cards").read()
#     except OSError:
#         return []
#     found = re.findall(r"^\s*\d+ \[(\S+?)\s*\]: USB-Audio - .*\n\s*.* at usb-[^-]+-([\d.]+)", cards, re.M)
#     return sorted(found, key=lambda c: [int(n) for n in c[1].split(".")])


# class SpeakerTest:
#     """A test tone on one channel of one USB sound card, for checking speakers and wiring. Runs its own `aplay`
#     (stereo, tone on the chosen side, silence on the other), independent of the main sound output."""

#     FREQ = 440.0
#     LEVEL = db(-6)
#     CHANNELS = ("left", "right")

#     def __init__(self):
#         self.target = None  # (card id, channel index)
#         self.on = False
#         self.error = None
#         self.thread = None
#         self.lock = threading.Lock()

#     def set_target(self, card, ch):
#         self.target = (card, ch)

#     def set_on(self, on):
#         with self.lock:
#             self.on = bool(on) and self.target is not None
#             if self.on and not (self.thread and self.thread.is_alive()):
#                 self.thread = threading.Thread(target=self._run, name="caltreat-speaker-test", daemon=True)
#                 self.thread.start()

#     def _run(self):
#         n = np.arange(BLOCK)
#         fade = np.minimum(np.arange(1, BLOCK + 1) / BLOCK, 1.0)  # one block of fade in/out to avoid clicks
#         pos = 0
#         while self.on:
#             card, ch = self.target
#             proc = subprocess.Popen(
#                 ["aplay", "-q", "-D", f"plughw:CARD={card}", "-t", "raw", "-f", "S16_LE", "-r", str(SR), "-c", "2",
#                  "--buffer-time=60000", "--period-time=15000"],
#                 stdin=subprocess.PIPE,
#                 stderr=subprocess.PIPE,
#             )
#             first = True
#             try:
#                 while True:
#                     ending = not self.on or self.target != (card, ch)
#                     tone = np.sin(2 * np.pi * self.FREQ * (pos + n) / SR) * self.LEVEL
#                     pos = (pos + BLOCK) % SR
#                     if first:
#                         tone *= fade
#                     elif ending:
#                         tone *= fade[::-1]
#                     first = False
#                     frames = np.zeros((BLOCK, 2))
#                     frames[:, ch] = tone
#                     proc.stdin.write((frames * 32767).astype("<i2").tobytes())
#                     if ending:
#                         break
#                 proc.stdin.close()
#                 proc.wait(timeout=2)
#                 self.error = None
#             except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
#                 proc.kill()
#                 self.error = proc.stderr.read().decode(errors="replace").strip() or "aplay exited"
#                 print(f"caltreat: speaker test failed: {self.error}", file=sys.stderr)
#                 self.on = False
#                 break


# audio = AudioOut()
# lights = Lights(audio)
# speaker_test = SpeakerTest()
# app.on_startup(audio.start)
# app.on_startup(lights.start)
# app.on_shutdown(audio.stop)
# app.on_shutdown(lights.stop)
# app.on_shutdown(lambda: speaker_test.set_on(False))


# def hold_button(btn, down, up):
#     btn.on("pointerdown", down)
#     for ev in ("pointerup", "pointercancel", "pointerleave"):
#         btn.on(ev, up)
#     btn.on("contextmenu", js_handler="(e) => e.preventDefault()")


# @ui.page("/")
# def index(client):
#     ui.dark_mode(True)
#     ui.add_css("""
#         .hold, .pad, .pat { touch-action: none; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none; }
#         .horn-btn.held { filter: brightness(1.35); transform: translateY(3px); }
#         .pad { min-height: 76px; border: 2px solid #2b3036; transition: transform .05s, background .1s; }
#         .pad:active { transform: scale(.98); }
#         .pad.bell.on { background: #3a2a0c !important; border-color: #f5a623; box-shadow: 0 0 18px rgba(245,166,35,.35); }
#         .pad.door.on { background: #0e3321 !important; border-color: #2fd07a; box-shadow: 0 0 18px rgba(47,208,122,.3); }
#         .pad.tone.on { background: #0e3321 !important; border-color: #2fd07a; box-shadow: 0 0 18px rgba(47,208,122,.3); }
#         .pat { border: 1px solid #3b424b; }
#         .pat.busy { border-color: #e1251b; }
#         .lamp i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #26302a; margin-right: 6px; }
#         .lamp.on.horn i { background: #e1251b; box-shadow: 0 0 10px #e1251b; }
#         .lamp.on.bell i { background: #f5a623; box-shadow: 0 0 10px #f5a623; }
#         .lamp.on.door i { background: #2fd07a; box-shadow: 0 0 10px #2fd07a; }
#         .lcd { background: #0a0f0c; color: #9ff0bd; font-family: ui-monospace, monospace; }
#         .lamp.on.white i { background: #f4f1e6; box-shadow: 0 0 12px #fffbe8; }
#         .lamp.on.red i { background: #e1251b; box-shadow: 0 0 10px #e1251b; }
#         .sw { display: flex; border: 2px solid #2b3036; border-radius: 10px; overflow: hidden; background: #14171b; }
#         .sw .q-btn { flex: 1; min-height: 48px; border-radius: 0; font-weight: 700; letter-spacing: .1em; }
#     """)
#     who = client.id

#     def press():
#         audio.hold(who, True)
#         horn_btn.classes(add="held")

#     def release():
#         audio.hold(who, False)
#         horn_btn.classes(remove="held")

#     client.on_disconnect(lambda: audio.release(who))

#     with ui.column().classes("w-full max-w-md mx-auto p-4 gap-4"):
#         ui.label("Caltreat").classes("text-3xl font-bold tracking-wider")

#         with ui.card().classes("w-full"):
#             vol_label = ui.label()
#             vol = ui.slider(min=0, max=100, step=1, value=get_volume()).props("label")

#             def on_volume(e):
#                 set_volume(e.value)
#                 vol_label.text = f"Headphone jack volume: {int(e.value)} %"

#             vol.on_value_change(on_volume)
#             vol_label.text = f"Headphone jack volume: {int(vol.value)} %"

#         # cab display
#         with ui.row().classes("lcd w-full rounded-lg p-3 items-end justify-between no-wrap"):
#             with ui.column().classes("gap-0"):
#                 ui.label("SPEED").classes("text-xs opacity-60")
#                 mph = ui.label().classes("text-3xl")
#             with ui.column().classes("gap-0"):
#                 ui.label("INVERTER").classes("text-xs opacity-60")
#                 hz = ui.label().classes("text-3xl")
#             with ui.column().classes("gap-1 text-xs"):
#                 lamps = {k: ui.html(f"<i></i>{k.upper()}").classes(f"lamp {k}") for k in ("horn", "bell", "door")}

#         # power controller
#         with ui.card().classes("w-full gap-1"):
#             thr_label = ui.label()
#             thr = ui.slider(min=0, max=100, step=1, value=round(audio.motor.throttle * 100))
#             with ui.row().classes("w-full justify-between text-xs opacity-60 px-2"):
#                 for n in ("OFF", "25", "50", "75", "FULL"):
#                     ui.label(n)

#             def on_throttle(e):
#                 audio.set_throttle(e.value / 100)
#                 thr_label.text = f"Power controller: {int(e.value)} %"

#             thr.on_value_change(on_throttle)
#             thr_label.text = f"Power controller: {int(thr.value)} %"

#         # pads
#         def pad(title, sub, cls):
#             with ui.button().classes(f"pad {cls} w-full rounded-xl").props("unelevated color=grey-10 no-caps align=left") as b:
#                 with ui.column().classes("gap-1 items-start text-left"):
#                     ui.label(title).classes("text-xl font-bold uppercase tracking-wide")
#                     ui.label(sub).classes("text-xs opacity-60")
#             return b

#         with ui.grid(columns=2).classes("w-full gap-3"):
#             bell_btn = pad("Bell", "Tap on / off · 2.5 strikes/s", "bell")
#             door_btn = pad("Doors", "Tap: 3 s · hold: as long as held", "door hold")
#             air_btn = pad("Air", "Brake release hiss", "")
#             brake_btn = pad("Brakes", "Squeal while stopping", "")
#         bell_btn.on("pointerdown", audio.toggle_bell)
#         hold_button(door_btn, lambda: audio.door_down(who), lambda: audio.door_up(who))
#         air_btn.on("pointerdown", audio.air)
#         brake_btn.on("pointerdown", audio.brakes)

#         horn_btn = ui.button("HORN").classes("horn-btn hold w-full h-40 text-5xl font-bold tracking-widest") \
#             .props("color=red unelevated")
#         hold_button(horn_btn, press, release)

#         # horn signals
#         with ui.card().classes("w-full gap-2"):
#             ui.label("Horn signals  (– long · o short)").classes("text-sm opacity-70")
#             with ui.grid(columns=2).classes("w-full gap-2"):
#                 pat_btns = []
#                 for i, (name, sig, _) in enumerate(PATTERNS):
#                     with ui.button().classes("pat w-full rounded-lg").props("unelevated color=grey-9 no-caps") as b:
#                         with ui.row().classes("w-full justify-between items-center no-wrap"):
#                             ui.label(name).classes("font-bold uppercase text-left")
#                             ui.label(sig).classes("font-mono text-red-500 tracking-widest whitespace-nowrap")
#                     b.on("pointerdown", lambda i=i: audio.play_pattern(i))
#                     pat_btns.append(b)

#         # lights (relay shield)
#         with ui.card().classes("w-full gap-3"):
#             ui.label("Lights").classes("text-sm opacity-70 uppercase tracking-widest")
#             with ui.row().classes("lcd w-full rounded-lg px-3 py-2 justify-between no-wrap text-xs"):
#                 light_lamps = [ui.html(f"<i></i>{name}").classes(f"lamp {color}") for name, color in
#                                (("HEAD L", "white"), ("HEAD R", "white"), ("MARK L", "red"), ("MARK R", "red"))]
#             switches = {}
#             for pair, title, sub, color, text in (("white", "Headlights", "Ch 1–2 · white · flash = ditch lights",
#                                                    "grey-2", "black"),
#                                                   ("red", "Marker lights", "Ch 3–4 · red · flash = alternate",
#                                                    "red", "white")):
#                 with ui.column().classes("w-full gap-1"):
#                     with ui.row().classes("w-full items-baseline justify-between"):
#                         ui.label(title).classes("text-xl font-bold uppercase tracking-wide")
#                         ui.label(sub).classes("text-xs opacity-60")
#                     sw = ui.toggle({m: m.upper() for m in Lights.MODES}, value=getattr(lights, pair)) \
#                         .classes("sw w-full") \
#                         .props(f"unelevated spread toggle-color={color} toggle-text-color={text} color=grey-10")
#                     sw.on_value_change(lambda e, pair=pair: setattr(lights, pair, e.value or "off"))
#                     switches[pair] = sw
#             horn_flash = ui.switch("Ditch lights flash with horn", value=lights.horn_flash) \
#                 .props("color=red").classes("text-sm")
#             horn_flash.on_value_change(lambda e: setattr(lights, "horn_flash", e.value))

#         # speaker test (USB sound cards)
#         with ui.card().classes("w-full gap-3"):
#             ui.label("Speaker test").classes("text-sm opacity-70 uppercase tracking-widest")
#             usb_cards = [card for card, _ in usb_outputs()]
#             if usb_cards:
#                 usb_vol_label = ui.label()
#                 usb_vol = ui.slider(min=0, max=100, step=1, value=get_volume(usb_cards[0], USB_MIXER_CONTROL)) \
#                     .props("label")

#                 def on_usb_volume(e):
#                     for card in usb_cards:
#                         set_volume(e.value, card, USB_MIXER_CONTROL)
#                     usb_vol_label.text = f"USB sound card volume: {int(e.value)} %"

#                 usb_vol.on_value_change(on_usb_volume)
#                 usb_vol_label.text = f"USB sound card volume: {int(usb_vol.value)} %"
#             outputs = {f"{card}:{i}": f"USB {port} ({card}) · {side.title()}"
#                        for card, port in usb_outputs() for i, side in enumerate(SpeakerTest.CHANNELS)}
#             if speaker_test.target and f"{speaker_test.target[0]}:{speaker_test.target[1]}" not in outputs:
#                 speaker_test.set_on(False)
#             current = f"{speaker_test.target[0]}:{speaker_test.target[1]}" if speaker_test.target else None
#             current = current if current in outputs else next(iter(outputs), None)

#             def pick(key):
#                 if key:
#                     card, ch = key.rsplit(":", 1)
#                     speaker_test.set_target(card, int(ch))

#             ch_select = ui.select(outputs, value=current, label="Channel").classes("w-full") \
#                 .props("outlined dark options-dense")
#             ch_select.on_value_change(lambda e: pick(e.value))
#             pick(current)
#             tone_btn = pad("Test tone", f"{SpeakerTest.FREQ:.0f} Hz · tap on / off", "tone")
#             tone_btn.on("pointerdown", lambda: speaker_test.set_on(not speaker_test.on))
#             if not outputs:
#                 ui.label("No USB sound cards found. Plug one in and reload.").classes("text-sm text-red-400")
#                 tone_btn.disable()

#         ui.label("Keys: Space/H horn (hold) · B bell · D doors (hold) · A air · S brakes · ↑↓ power · "
#                  "0 power off · 1–6 horn signals · L headlights · M markers").classes("text-sm opacity-70")

#         status = ui.label().classes("text-sm text-red-400")

#         def show_errors():
#             errs = [f"Audio output problem: {audio.error}" if audio.error else "",
#                     f"Relay problem: {lights.error}" if lights.error else "",
#                     f"Speaker test problem: {speaker_test.error}" if speaker_test.error else ""]
#             status.set_text(" · ".join(e for e in errs if e))

#         ui.timer(1.0, show_errors)

#     # Reflect the shared engine state (other browsers may be pressing things too); only send changes.
#     shown = {}

#     def show(key, el, cls, on):
#         if shown.get(key) != on:
#             shown[key] = on
#             el.classes(add=cls) if on else el.classes(remove=cls)

#     def tick():
#         mph.text = f"{round(audio.motor.speed * 79)} mph"
#         hz.text = f"{round(audio.motor.speed * MOTOR['a_max'])} Hz"
#         show("lh", lamps["horn"], "on", audio.horn_on)
#         show("lb", lamps["bell"], "on", audio.bell.on)
#         show("ld", lamps["door"], "on", audio.door_on)
#         show("bell", bell_btn, "on", audio.bell.on)
#         show("door", door_btn, "on", audio.door_on)
#         busy = audio.busy_pattern
#         for i, b in enumerate(pat_btns):
#             show(f"p{i}", b, "busy", busy == i)
#         if round(thr.value) != round(audio.motor.throttle * 100):
#             thr.value = round(audio.motor.throttle * 100)
#         for i, lamp in enumerate(light_lamps):
#             show(f"r{i}", lamp, "on", lights.state[i])
#         for pair, sw in switches.items():
#             if sw.value != getattr(lights, pair):
#                 sw.value = getattr(lights, pair)
#         if horn_flash.value != lights.horn_flash:
#             horn_flash.value = lights.horn_flash
#         show("tone", tone_btn, "on", speaker_test.on)
#         if speaker_test.target:
#             key = f"{speaker_test.target[0]}:{speaker_test.target[1]}"
#             if key != ch_select.value and key in outputs:
#                 ch_select.value = key

#     ui.timer(0.1, tick)

#     held = set()

#     def on_key(e):
#         if e.action.repeat or e.modifiers.ctrl or e.modifiers.meta or e.modifiers.alt:
#             return
#         k = e.key.name.lower()
#         if k in (" ", "h"):
#             if e.action.keydown:
#                 held.add(k)
#                 press()
#             else:
#                 held.discard(k)
#                 if not held:
#                     release()
#         elif k == "d":
#             audio.door_down(who) if e.action.keydown else audio.door_up(who)
#         elif not e.action.keydown:
#             return
#         elif k == "b":
#             audio.toggle_bell()
#         elif k == "a":
#             audio.air()
#         elif k == "s":
#             audio.brakes()
#         elif e.key.arrow_up:
#             thr.value = min(100, thr.value + 10)
#         elif e.key.arrow_down:
#             thr.value = max(0, thr.value - 10)
#         elif k == "0":
#             thr.value = 0
#         elif len(k) == 1 and k in "123456":
#             audio.play_pattern(int(k) - 1)
#         elif k == "l":
#             lights.cycle("white")
#         elif k == "m":
#             lights.cycle("red")

#     ui.keyboard(on_key=on_key, ignore=["input", "select", "textarea"])


# def main():
#     ui.run(host="0.0.0.0", port=8080, title="Caltreat", reload=False, show=False)


# if __name__ in {"__main__", "__mp_main__"}:
#     main()






"""Caltreat: the train's web app. NiceGUI pages for the engineer (/) and for the settings (/settings), the Caltrain
KISS EMU sound engine, the speakers, the lights and the push-to-talk intercom.

The voices are numpy ports of the sound engine in publish.html: horn (one harmonic series near F#4 with its
measured harmonic levels, onset pitch overshoot, one-pole attack/release and faint air noise), the electronic
bell, the door-closing tone, the traction inverter whine, the air-release hiss and the brake squeal, plus the
horn signal patterns. Audio is synthesized in blocks; `Router` plays the mix on the configured speakers (FRONT,
INSIDE, REAR: the 3.5 mm jack or a channel of a USB sound card) by piping raw PCM into one `aplay` per sound card, so
no PortAudio or other system packages are needed. The intercom (intercom.py) plays on INSIDE, which is ducked while
someone talks. All browsers share the one engine, so its state (bell, doors, throttle, sequences, ...) is shared too.

The Depart / Arrive / Crossing sequences (sequencer.py) follow GCOR 5.8 and 49 CFR 222.21. Lights: the relay shield
(white head/ditch lights, red markers by default) and GP0-GP7 of the CalTreat lighting controllers (Waveshare
RP2350-ETH, Modbus TCP) found on the Pi's wired network; every output is mapped to a light function in the settings.
Everything configurable is in one JSON file (config.py), which the settings page can download and restore.
"""

import asyncio
import collections
import contextlib
import fcntl
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import WebSocket, WebSocketDisconnect
from nicegui import app, ui

from . import hw
from .config import BOARD_PINS, BUILTIN_FUNCTIONS, CHANNELS, MIX_KEYS, RELAY_CHANNELS, SPEAKERS, Config, board_defaults, diff, parse
from .intercom import Intercom
from .sequencer import SIGNALS, Sequencer, signal_pattern

SR = hw.SR
BLOCK = 512  # ~11 ms per synthesized block
MASTER = 1.1 * 0.85  # publish.html's default master volume, before its soft knee
# publish.html's default mix levels
HORN_INTENSITY = 0.85
BELL_LEVEL = 0.75
MOTOR_LEVEL = 0.8
DOOR_LEVEL = 0.7
HISS_LEVEL = 0.7
ACCEL_TIME = 14.0  # s, standstill to full ("Costume")
ACCEL_TIME_REAL = 60.0  # s, the real train
BRAKE_TIME = 10.0  # s, full speed to a halt with STOP ("Costume")
BRAKE_TIME_REAL = 35.0  # s (~1 m/s², a service brake)
LOOP = 1 << 18  # ~5.5 s noise loops
PORT = int(os.environ.get("CALTREAT_PORT", "8080"))
HTTPS_PORT = int(os.environ.get("CALTREAT_HTTPS_PORT", "8443"))
STATIC = Path(__file__).parent / "static"

# Lighting controller boards get DHCP addresses on eth0 (NetworkManager's shared connection: the Pi is
# 192.168.50.1 and its dnsmasq leases .10-.254). They are found by scanning that range, and named A, B, ... by chip
# ID in the order they are first seen; names and output mappings are kept in the config.
REMOTE_NET = os.environ.get("CALTREAT_BOARD_NET", "192.168.50.")
REMOTE_HOSTS = range(10, 255)
REMOTE_PORT = int(os.environ.get("CALTREAT_BOARD_PORT", "502"))
REMOTE_PINS = BOARD_PINS  # GP0-GP7 (GP0-GP3 drive the MOSFET module's lights 1-4)

# Fitted to recordings of Caltrain Stadler KISS EMUs (publish.html KISS_PARAMS).
HORN = dict(
    f0=376.2,
    harm=[-0.4, 0.0, -5.6, -8.1, -13.2, -12.9, -16.5, -23.4, -23.9, -22.3, -23.5, -24.5],
    bend=56.7,
    bend_tau=0.15,
    atk=0.061,
    rel=0.293,
    noise=-55.9,
)
BELL = dict(
    period=0.4,
    partials=[[805.0, -10.6, -37.6], [1387.5, 0.0, -4.1], [2035.0, -6.2, -23.8], [2737.5, -18.1, -24.0],
              [3495.0, -16.0, -29.9], [4312.5, -27.0, -65.2], [5202.5, -9.2, -147.9]],  # Hz, dB, dB/s
)
DOOR = dict(f0=1760, harm=[-0.8, 0.0, -18.9, -24.0])
MOTOR = dict(a_max=980, c_slope=2.144, c_off=34.3, b_slope=2.985, b_off=745.2, d_slope=0.181, carrier=2022,
             car_slope=0.419, lv_c=-7.4, lv_car=-2.8, lv_b=-12, lv_d=-8)
HISS = dict(whistle=7214, dur=3.0)
SQUEAL = dict(f=2150, wobble=125, dur=3.5)

# Horn signals on the engineer page (sequencer.SIGNALS, GCOR 5.8.2) plus the costume's own show pattern.
SHOW = ("show", "Show", "o o – o –", "Horn show: a fun pattern for the costume, not a railroad signal")
SHOW_PATTERN = [(0, 0.35), (0.6, 0.35), (1.2, 1.4), (3.0, 0.35), (3.6, 2.2)]
HORN_SIGNALS = [*SIGNALS, SHOW]
BUILTIN_IDS = {f[0] for f in BUILTIN_FUNCTIONS}


def db(d):
    return 10.0 ** (np.asarray(d) / 20.0)


def tau_coef(tau):
    return 1.0 - np.exp(-1.0 / (SR * tau))


def ramp(x, a, b):
    """0 below a, 1 above b."""
    return min(max((x - a) / (b - a), 0.0), 1.0)


def biquad(kind, f, q, gain_db=0.0):
    """RBJ cookbook coefficients (b, a) with a[0] == 1, as the JS `Biquad`."""
    w = 2 * np.pi * min(f, SR * 0.45) / SR
    c, al, A = np.cos(w), np.sin(w) / (2 * q), 10 ** (gain_db / 40)
    if kind == "lp":
        b, a = [(1 - c) / 2, 1 - c, (1 - c) / 2], [1 + al, -2 * c, 1 - al]
    elif kind == "hp":
        b, a = [(1 + c) / 2, -(1 + c), (1 + c) / 2], [1 + al, -2 * c, 1 - al]
    elif kind == "bp":
        b, a = [al, 0.0, -al], [1 + al, -2 * c, 1 - al]
    else:  # peak
        b, a = [1 + al * A, -2 * c, 1 - al * A], [1 + al / A, -2 * c, 1 - al / A]
    return [x / a[0] for x in b], [x / a[0] for x in a]


def one_pole(k):
    """y += (x - y) * k"""
    return [k], [1.0, -(1 - k)]


def filtered_noise_loop(rng, n, *sections):
    """Uniform noise in [-1, 1) through the cascaded IIR sections (b, a), shaped in the frequency domain so the
    buffer loops seamlessly. Stands in for the per-sample noise filters of the JS engine."""
    spectrum = np.fft.rfft(rng.uniform(-1.0, 1.0, n))
    z = np.exp(-1j * 2 * np.pi * np.arange(len(spectrum)) / n)
    for b, a in sections:
        spectrum *= np.polyval(b[::-1], z) / np.polyval(a[::-1], z)
    return np.fft.irfft(spectrum, n)


def one_pole_env(env, tgt, pow_n):
    """Per-sample one-pole glide from env towards tgt over a block, given (1 - coef) ** [1..BLOCK]."""
    return tgt + (env - tgt) * pow_n


class Horn:
    """Block-rendered port of the JS `Horn`; the gate is sampled once per block."""

    def __init__(self, intensity=HORN_INTENSITY, seed=12345):
        P = HORN
        rng = np.random.default_rng(seed)
        tilt = (1 - intensity) * 1.6  # louder = brighter: tilt the upper harmonics
        self.hk = db([d - tilt * i for i, d in enumerate(P["harm"])])[:, None]
        self.k = np.arange(1, len(P["harm"]) + 1)[:, None]
        self.norm = 1.5 / self.hk.sum()
        self.level = 0.15 + 0.85 * intensity
        self.bend_depth = P["bend"] * (0.6 + 0.4 * intensity)
        n = np.arange(1, BLOCK + 1)
        self.atk_pow = (1 - tau_coef(P["atk"] / 2.197)) ** n
        self.rel_pow = (1 - tau_coef(P["rel"] / 2.197)) ** n
        self.t = np.arange(BLOCK) / SR
        self.jitter = filtered_noise_loop(rng, LOOP, one_pole(tau_coef(0.08)))
        self.breath = filtered_noise_loop(rng, LOOP, biquad("bp", 2500, 0.6))
        self.pos = 0
        self.env = 0.0
        self.gate = False
        self.phase = 0.0
        self.bend_t = 1e9

    def set_gate(self, on):
        if on and not self.gate and self.env < 0.35:
            self.bend_t = 0.0
        self.gate = on

    def render(self, t0):
        if not self.gate and self.env == 0.0:
            return None
        P = HORN
        tgt = 1.0 if self.gate else 0.0
        env = one_pole_env(self.env, tgt, self.atk_pow if tgt > self.env else self.rel_pow)
        self.env = 0.0 if not self.gate and env[-1] < 1e-5 else float(env[-1])

        idx = (self.pos + np.arange(BLOCK)) % LOOP
        self.pos = (self.pos + BLOCK) % LOOP
        bt = self.bend_t + self.t
        self.bend_t += BLOCK / SR
        bend = self.bend_depth * np.exp(-bt / P["bend_tau"])
        f = P["f0"] * 2.0 ** ((bend + self.jitter[idx] * 8) / 1200)
        ph = self.phase + np.cumsum(f / SR)
        self.phase = float(ph[-1] % 1.0)

        tone = (self.hk * np.sin(2 * np.pi * self.k * ph)).sum(axis=0)
        breath = self.breath[idx] * (db(P["noise"] + 12) + 0.03 * np.exp(-bt / 0.05))
        return (tone * self.norm + breath) * env * self.level


class Bell:
    """Port of the JS `Bell`: identical strikes every `period`, each a bank of decaying partials plus a short
    5-9 kHz click; a new strike fades the previous one out in a few ms. Strikes are sample-accurate."""

    def __init__(self, rng, level=BELL_LEVEL):
        f, lv, dec = np.array(BELL["partials"]).T
        self.period = round(BELL["period"] * SR)
        self.w = (2 * np.pi * f / SR)[:, None]
        self.amp = db(lv)[:, None]
        self.log_decay = (dec / 20 / SR * np.log(10))[:, None]  # per-sample ln of the amplitude decay
        self.norm = 0.8 / db(lv).sum()
        self.level = level
        self.click = filtered_noise_loop(rng, LOOP, biquad("bp", 6500, 1.2))
        self.click_len = int(np.log(1e4) * 0.004 * SR)  # samples until the click is below -80 dB
        self.fade_fast = np.exp(-1 / (SR * 0.0025))
        self.fade_release = np.exp(-1 / (SR * 0.12))
        self.on = False
        self.next = None  # absolute sample of the next strike; None = at the start of the next block
        self.strikes = []  # [start, fade_at or None, per-sample fade multiplier]

    def set_on(self, on):
        if on and not self.on:
            self.next = None
        if not on and self.on and self.next is not None and self.strikes and self.strikes[-1][1] is None:
            self.strikes[-1][1:] = [self.next, self.fade_release]  # ring to the end of this period, then release
        self.on = on

    def _strike(self, at):
        for s in self.strikes:
            if s[1] is None or s[1] > at:
                s[1:] = [at, self.fade_fast]
        self.strikes.append([at, None, 1.0])

    def render(self, t0):
        if self.on:
            if self.next is None:
                self.next = t0
            while self.next < t0 + BLOCK:
                self._strike(self.next)
                self.next += self.period
        if not self.strikes:
            return None
        idx = t0 + np.arange(BLOCK)
        y = np.zeros(BLOCK)
        for s in list(self.strikes):
            start, fade_at, fm = s
            k = idx - start
            live = k >= 0
            k = np.maximum(k, 0)
            v = (np.sin(self.w * (k + 1)) * self.amp * np.exp(self.log_decay * k)).sum(axis=0)
            if k[0] < self.click_len:
                v += self.click[idx % LOOP] * np.exp(-k / (SR * 0.004)) * 0.9
            v *= np.minimum(k / 48, 1.0) * live
            if fade_at is not None:
                fade = fm ** np.maximum(idx - fade_at + 1, 0)
                v *= fade
                if fade[-1] < 1e-4:
                    self.strikes.remove(s)
            y += v
        return y * self.norm * self.level


class Door:
    """Door-closing warning: continuous A6 (1760 Hz) with a strong 2nd harmonic, gated."""

    def __init__(self, level=DOOR_LEVEL):
        self.h = db(DOOR["harm"])[:, None]
        self.k = np.arange(1, len(DOOR["harm"]) + 1)[:, None]
        self.norm = 0.5 / self.h.sum()
        self.w = 2 * np.pi * DOOR["f0"] / SR
        self.pow = (1 - tau_coef(0.004)) ** np.arange(1, BLOCK + 1)
        self.level = level
        self.gate = False
        self.env = 0.0
        self.phase = 0.0

    def render(self, t0):
        if not self.gate and self.env == 0.0:
            return None
        env = one_pole_env(self.env, 1.0 if self.gate else 0.0, self.pow)
        self.env = 0.0 if not self.gate and env[-1] < 1e-5 else float(env[-1])
        ph = self.phase + self.w * np.arange(1, BLOCK + 1)
        self.phase = float(ph[-1] % (2 * np.pi))
        return (self.h * np.sin(self.k * ph)).sum(axis=0) * self.norm * env * self.level


class Motor:
    """Traction inverter / motor whine. `throttle` sets the target speed; speed follows at a limited rate. Line A
    (0-980 Hz) is the speed proxy; the other lines are fitted functions of A. The JS engine's 64-sample control
    step runs once per block here, with frequencies and levels interpolated across the block."""

    STEPS = BLOCK / 64  # JS control steps per block

    def __init__(self, rng, level=MOTOR_LEVEL):
        self.level = level
        self.throttle = 0.0
        self.speed = 0.0
        self.load = 0.0
        self.gain = 0.0
        self.accel_time = ACCEL_TIME
        self.coast_time = 30.0
        self.brake_time = BRAKE_TIME  # STOP: full speed to a halt
        self.braking = False
        self.f = np.zeros(9)
        self.a = np.zeros(9)
        self.ph = np.zeros(9)
        self.rumble = filtered_noise_loop(rng, LOOP, biquad("lp", 220, 0.7))
        self.r = (np.arange(1, BLOCK + 1) / BLOCK)

    @staticmethod
    def k(per_step):
        return 1 - (1 - per_step) ** Motor.STEPS

    def control(self):
        dt, P = BLOCK / SR, MOTOR
        if self.throttle > self.speed:
            self.speed = min(self.throttle, self.speed + dt / self.accel_time)
        else:
            self.speed = max(self.throttle, self.speed - dt / (self.brake_time if self.braking else self.coast_time))
        if self.throttle > 0.01:
            motoring = min(max(0.35 + (self.throttle - self.speed) * 3 + 0.3 * self.throttle, 0.0), 1.0)
        else:
            motoring = 0.18 * ramp(self.speed, 0, 0.05)
        self.load += (motoring - self.load) * self.k(0.02)
        A = P["a_max"] * self.speed
        C = P["c_slope"] * A + P["c_off"]
        car = P["carrier"] + P["car_slope"] * A
        fan = 0.2 * C * ramp(A, 20, 80)
        idle = 1.0 if self.throttle > 0.01 or self.speed > 0.002 else 0.0
        lo = 1 - ramp(A, 500, 600)
        self.f = np.array([A, C, car, car + fan, car - fan, car + 2 * fan, car - 2 * fan,
                           P["b_slope"] * A + P["b_off"], P["d_slope"] * A])
        target = np.array([
            ramp(A, 60, 300),
            db(P["lv_c"]) * ramp(A, 15, 60) * (1 - ramp(A, 550, 650)),
            db(P["lv_car"]) * lo * idle,
            db(P["lv_car"] - 6) * ramp(fan, 5, 40) * lo,
            db(P["lv_car"] - 6) * ramp(fan, 5, 40) * lo,
            db(P["lv_car"] - 30) * ramp(fan, 10, 60) * lo,
            db(P["lv_car"] - 30) * ramp(fan, 10, 60) * lo,
            db(P["lv_b"]) * ramp(A, 500, 600),
            db(P["lv_d"]) * ramp(A, 20, 120),
        ])
        self.a = self.a + (target - self.a) * self.k(0.05)
        self.gain += (self.load - self.gain) * self.k(0.05)

    def render(self, t0):
        f0, a0, g0 = self.f, self.a, self.gain
        self.control()
        if self.gain < 1e-4 and g0 < 1e-4 and self.speed < 1e-4:
            return None
        f = f0[:, None] + (self.f - f0)[:, None] * self.r
        a = a0[:, None] + (self.a - a0)[:, None] * self.r
        ph = self.ph[:, None] + np.cumsum(f / SR, axis=1)
        self.ph = ph[:, -1] % 1.0
        live = (np.maximum(a0, self.a) >= 1e-5) & (self.f > 0) & (self.f < SR * 0.45)
        y = (a[live] * np.sin(2 * np.pi * ph[live])).sum(axis=0)
        g = g0 + (self.gain - g0) * self.r
        rumble = self.rumble[(t0 + np.arange(BLOCK)) % LOOP] * 0.12 * self.speed
        return (y * 0.22 * g + rumble) * self.level


class OneShot:
    """Base for triggered sounds: `start` is the absolute sample of the last trigger, or None when silent."""

    def __init__(self):
        self.start = None
        self.pending = False

    def trigger(self):
        self.pending = True

    def times(self, t0):
        if self.pending:
            self.start, self.pending = t0, False
        if self.start is None:
            return None
        return (t0 + np.arange(BLOCK) - self.start) / SR


class Hiss(OneShot):
    """Air release: broadband hiss (strongest 2.5-4 kHz) with a faint 7.2 kHz whistle; a one-shot of `dur` s."""

    def __init__(self, rng, level=HISS_LEVEL):
        super().__init__()
        self.level = level
        self.noise = filtered_noise_loop(rng, LOOP, biquad("hp", 1800, 0.7), biquad("peak", 3200, 0.8, 8),
                                         biquad("lp", 12000, 0.7))

    def render(self, t0):
        t = self.times(t0)
        if t is None:
            return None
        d = HISS["dur"]
        if t[0] > d + 0.8:
            self.start = None
            return None
        env = np.minimum(1, t / 0.08) * np.where(t < d, 1 - 0.25 * t / d, 0.75 * np.exp(-(t - d) / 0.18))
        n = self.noise[(t0 + np.arange(BLOCK)) % LOOP]
        whistle = np.sin(2 * np.pi * HISS["whistle"] * (t0 + np.arange(BLOCK)) / SR)
        return (n * 0.35 + 0.02 * whistle) * env * self.level


class Squeal(OneShot):
    """Brake squeal: a wobbling ~2150 Hz tone (smoothed random frequency walk) with a weaker upper partial."""

    def __init__(self, rng, level=HISS_LEVEL * 0.7):
        super().__init__()
        self.level = level
        self.wobble = filtered_noise_loop(rng, LOOP, ([0.6 * 0.0006], [1.0, -(1 - 0.0006)]), one_pole(0.0015))
        self.ph = 0.0
        self.ph2 = 0.0

    def render(self, t0):
        t = self.times(t0)
        if t is None:
            return None
        P = SQUEAL
        d = P["dur"]
        if t[0] > d:
            self.start = None
            return None
        f = P["f"] + self.wobble[(t0 + np.arange(BLOCK)) % LOOP] * P["wobble"] * 4
        ph = self.ph + np.cumsum(f / SR)
        ph2 = self.ph2 + np.cumsum(1.44 * f / SR)
        self.ph, self.ph2 = float(ph[-1] % 1.0), float(ph2[-1] % 1.0)
        env = np.clip(np.minimum(t / 0.3, (d - t) / 0.4), 0, 1) * (0.7 + 0.3 * np.sin(2 * np.pi * 1.7 * t))
        return (np.sin(2 * np.pi * ph) + 0.25 * np.sin(2 * np.pi * ph2)) * 0.4 * env * self.level


def soft_knee(v):
    """Linear below 0.6, tanh knee above (as the JS engine's output stage)."""
    av = np.abs(v)
    return np.where(av < 0.6, v, np.sign(v) * (0.6 + 0.4 * np.tanh((av - 0.6) / 0.4)))



class AudioOut:
    """The engine: all voices and the controls on them, rendered one block at a time by `Router`. Controls may be
    called from any thread. Besides the sounds it keeps the doors (closed / opening / open / closing and the leaf
    position) and the brakes (STOP brakes the train to a halt: squeal near the end, then the air), which the page
    and the sequences use."""

    DOOR_TONE = 3.0  # s of warning tone when the doors close (or as long as the press is held)
    DOOR_WARN = 1.0  # s of tone before the leaves start to move
    DOOR_SLIDE = 2.5  # s for the leaves to travel fully

    def __init__(self):
        rng = np.random.default_rng(54321)
        self.horn = Horn()
        self.bell = Bell(rng)
        self.door = Door()
        self.motor = Motor(rng)
        self.hiss = Hiss(rng)
        self.squeal = Squeal(rng)
        self.voices = [self.horn, self.bell, self.door, self.motor, self.hiss, self.squeal]
        self.base = {"horn": self.horn.level, "bell": self.bell.level, "motor": self.motor.level,
                     "doors": self.door.level, "air": self.hiss.level, "brakes": self.squeal.level}
        self.master = MASTER
        self.lock = threading.Lock()
        self.t = 0  # absolute sample clock
        self.holders = set()  # clients holding the horn
        self.door_holders = set()  # clients holding a door press that closes the doors (tone while held)
        self.door_until = 0.0  # monotonic time the closing tone ends
        self.door_move_at = 0.0  # monotonic time the closing leaves start to move
        self.doors = "closed"
        self.door_pos = 0.0  # 0 closed .. 1 open
        self.door_tone = False
        self.pattern = []  # [(start, end) absolute samples]
        self.pattern_key = None
        self.braking = False
        self.squeal_done = False
        self.air_at = None
        self.timing = "costume"

    # ---------- controls ----------
    def hold(self, who, on):
        with self.lock:
            if on:
                self.pattern = []  # holding the horn cancels a running signal
                self.holders.add(who)
            else:
                self.holders.discard(who)

    def set_bell(self, on):
        with self.lock:
            self.bell.set_on(bool(on))

    def toggle_bell(self):
        with self.lock:
            self.bell.set_on(not self.bell.on)

    def _open(self):
        self.doors = "opening"
        self.door_until = 0.0
        self.door_holders.clear()

    def _close(self, now):
        self.doors = "closing"
        self.door_until = now + self.DOOR_TONE
        self.door_move_at = now + self.DOOR_WARN

    def door_press(self, who):
        """The DOORS pad: open if closed, close if open; a press while they move reverses them (as an obstruction).
        Closing sounds the warning tone for DOOR_TONE s, or as long as the press is held."""
        now = time.monotonic()
        with self.lock:
            if self.doors in ("open", "opening"):
                self._close(now)
                self.door_holders.add(who)
            else:
                self._open()

    def door_release(self, who):
        with self.lock:
            self.door_holders.discard(who)

    def doors_cmd(self, v):
        """From a sequence: "open" or "close" (nothing if they already are, or are on the way)."""
        now = time.monotonic()
        with self.lock:
            if v == "close" and self.doors in ("open", "opening"):
                self._close(now)
            elif v == "open" and self.doors in ("closed", "closing"):
                self._open()

    def air(self):
        self.hiss.trigger()

    def stop_train(self):
        """STOP: power off and brake to a halt (squeal for the last seconds, then the air). Standing: the squeal."""
        with self.lock:
            self.motor.throttle = 0.0
            if self.motor.speed > 0.003:
                self.braking, self.squeal_done = True, False
            else:
                self.squeal.trigger()

    def set_throttle(self, v):
        v = min(max(float(v), 0.0), 1.0)
        with self.lock:
            self.motor.throttle = v
            if v > 0.01:
                self.braking = False

    def play_seq(self, seq, key=None):
        with self.lock:
            self.pattern = [(self.t + round(s * SR), self.t + round((s + d) * SR)) for s, d in seq]
            self.pattern_key = key

    def stop_pattern(self):
        with self.lock:
            self.pattern = []

    def set_timing(self, mode):
        self.timing = "real" if mode == "real" else "costume"
        real = self.timing == "real"
        self.motor.accel_time = ACCEL_TIME_REAL if real else ACCEL_TIME
        self.motor.brake_time = BRAKE_TIME_REAL if real else BRAKE_TIME

    def apply_mix(self, mix):
        """mix: % of each voice's default level (config "mix")."""
        with self.lock:
            self.master = MASTER * mix.get("master", 100) / 100
            for k, v in (("horn", self.horn), ("bell", self.bell), ("motor", self.motor), ("doors", self.door),
                         ("air", self.hiss), ("brakes", self.squeal)):
                v.level = self.base[k] * mix.get(k, 100) / 100

    def release(self, who):
        """Never leave the horn or door tone stuck on when a client goes away."""
        with self.lock:
            self.holders.discard(who)
            self.door_holders.discard(who)

    # ---------- state for the UI ----------
    @property
    def horn_on(self):
        return self.horn.gate

    @property
    def busy_pattern(self):
        return self.pattern_key if self.pattern else None

    def state(self):
        return {"speed": self.motor.speed, "doors": self.doors}

    # ---------- rendering (Router's thread) ----------
    def _doors(self, now):
        self.door_tone = self.doors == "closing" and (bool(self.door_holders) or now < self.door_until)
        step = BLOCK / SR / self.DOOR_SLIDE
        if self.doors == "opening":
            self.door_pos = min(1.0, self.door_pos + step)
            if self.door_pos >= 1.0:
                self.doors = "open"
        elif self.doors == "closing":
            if now >= self.door_move_at:
                self.door_pos = max(0.0, self.door_pos - step)
            if self.door_pos <= 0.0 and not self.door_tone:
                self.doors = "closed"

    def _brakes(self):
        m = self.motor
        if self.braking:
            if m.throttle > 0.01:
                self.braking = False
            elif m.speed <= 0.0:
                self.braking = False
                self.air_at = self.t + int(0.8 * SR)  # the air release once standing
            elif not self.squeal_done and m.speed < 2.0 / m.brake_time:  # ~2 s before the halt
                self.squeal.trigger()
                self.squeal_done = True
        m.braking = self.braking
        if self.air_at is not None and self.t >= self.air_at:
            self.hiss.trigger()
            self.air_at = None

    def render(self):
        with self.lock:
            t0 = self.t
            self._doors(time.monotonic())
            self._brakes()
            while self.pattern and t0 >= self.pattern[0][1]:
                self.pattern.pop(0)
            pat = bool(self.pattern) and t0 >= self.pattern[0][0]
            self.horn.set_gate(bool(self.holders) or pat)
            self.door.gate = self.door_tone
            out = np.zeros(BLOCK)
            for v in self.voices:
                y = v.render(t0)
                if y is not None:
                    out += y
            self.t += BLOCK
            master = self.master
        return soft_knee(out * master)


class Device:
    """One sound card's player: its own thread writes the blocks `Router` queues to an `aplay` (2 channels),
    restarting it if the card goes away. A missing block is played as silence; extra blocks (clock drift between
    cards) are dropped, so a slow or missing card never holds up the others."""

    QUEUE = 1  # blocks kept ahead (~11 ms), on top of the pipe (~21 ms) and the ALSA buffer

    def __init__(self, key, card):
        self.key, self.card = key, card  # config device ("jack", "usb:1.2"), ALSA card id
        self.q = []
        self.cond = threading.Condition()
        self.running = True
        self.ok = False
        self.error = None
        self.underruns = 0
        self.dropped = 0
        self.blocks = 0  # blocks written to the current player
        self.written = collections.deque(maxlen=6000)  # (engine sample clock, block index), fake-audio runs only
        self.thread = threading.Thread(target=self._run, name=f"caltreat-out-{key}", daemon=True)
        self.thread.start()

    def push(self, frames, t=None):
        with self.cond:
            self.q.append((frames, t))
            if len(self.q) > self.QUEUE + 1:  # a card behind the pacing one (clock drift, start-up)
                del self.q[0]
                self.dropped += 1
                self.dropped += 1
            self.cond.notify_all()

    def wait_room(self, timeout):
        """For the clock master: wait until the card has taken enough to want another block."""
        with self.cond:
            return self.cond.wait_for(lambda: len(self.q) < self.QUEUE or not self.running, timeout)

    def stop(self):
        self.running = False
        with self.cond:
            self.cond.notify_all()

    def _pop(self):
        with self.cond:
            self.cond.wait_for(lambda: self.q or not self.running, 0.03)
            frames, t = self.q.pop(0) if self.q else (None, None)
            self.cond.notify_all()
        if frames is None:
            self.underruns += 1
            frames = np.zeros((BLOCK, 2))
        elif hw.FAKE_DIR:
            self.written.append((t, self.blocks))  # engine sample clock -> block in the recording (tests)
        self.blocks += 1
        return (np.clip(frames, -1, 1) * 32767).astype("<i2").tobytes()

    def _run(self):
        name = self.key.replace(":", "-").replace("#", "")
        while self.running:
            try:
                proc = hw.player(self.card, 2, name)
            except OSError as e:
                self.ok, self.error = False, str(e)
                time.sleep(2)
                continue
            try:
                fcntl.fcntl(proc.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)  # keep the pipe from adding latency
            except OSError:
                pass
            err = ""
            self.blocks = 0
            self.written.clear()
            try:
                while self.running:
                    data = memoryview(self._pop())
                    while data:  # unbuffered pipe: a write may (rarely) be partial
                        data = data[proc.stdin.write(data):]
                    self.ok, self.error = True, None
            except (BrokenPipeError, OSError):
                pass
            finally:
                self.ok = False
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                err = proc.stderr.read().decode(errors="replace").strip()
            if self.running:
                self.error = err or "player exited"
                print(f"caltreat: sound card {self.key} ({self.card}) failed, retrying: {self.error}", file=sys.stderr)
                time.sleep(2)


class Router:
    """Renders the engine and plays it on the speakers in the config: FRONT, INSIDE and REAR each map to a sound
    card (the jack or a USB card, by port) and a channel (left, right or both), with a level. The engine is one mono
    mix (the same on every speaker); INSIDE also gets the intercom, with the train sounds ducked while someone talks.
    The speaker test tone plays on any card and channel. INSIDE's card (or the jack, or any) sets the pace;
    with no card at all the engine still runs on the clock (so the train keeps moving and sequences keep going)."""

    TEST_FREQ = 440.0
    TEST_LEVEL = 10 ** (-6 / 20)
    REPLAN = 2.0  # s between checks for plugged / unplugged cards

    def __init__(self, audio, intercom, cfg):
        self.audio, self.intercom, self.cfg = audio, intercom, cfg
        self.devices = {}  # config device key -> Device
        self.plan = {}  # card key -> [(speaker, channel mask, gain)]
        self.status = {}  # speaker -> {"device", "card", "desc", "ok"}
        self.test = None  # (device key, "left" / "right" / "both")
        self.test_pos = 0
        self.test_env = 0.0
        self.duck = 1.0
        self.running = False
        self.thread = None
        self.dirty = True
        self.error = None
        cfg.on_change(self.replan)

    def replan(self):
        self.dirty = True

    def set_test(self, device, ch):
        """Tone on (device key, channel) or off (None)."""
        self.test = (device, ch) if device else None
        self.dirty = True

    def _plan(self):
        usb = hw.usb_outputs()
        plan, status = {}, {}
        for sp in SPEAKERS:
            s = self.cfg.data["speakers"][sp]
            card, desc = hw.resolve(s["device"], usb)
            key = None
            if card:
                key = hw.JACK if card == hw.JACK_CARD else "usb:" + next(p for c, p in usb if c == card)
                plan.setdefault(key, {"card": card, "outs": []})["outs"].append(
                    (sp, _mask(s["ch"]), s["level"] / 100))
            status[sp] = {"device": s["device"], "key": key, "card": card, "desc": desc,
                          "ok": None if s["device"] == "none" else card is not None}
        if self.test:
            card, _ = hw.resolve(self.test[0], usb)
            if card:
                key = hw.JACK if card == hw.JACK_CARD else self.test[0]
                if key.startswith("usb#"):
                    key = "usb:" + next(p for c, p in usb if c == card)
                plan.setdefault(key, {"card": card, "outs": []})
                self.test_key = key
            else:
                self.test_key = None
        self.plan, self.status = plan, status
        for key in list(self.devices):
            if key not in plan or self.devices[key].card != plan[key]["card"]:
                self.devices.pop(key).stop()
        for key, p in plan.items():
            if key not in self.devices:
                self.devices[key] = Device(key, p["card"])

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, name="caltreat-audio", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        for d in self.devices.values():
            d.stop()

    def _tone(self):
        on = self.test is not None and getattr(self, "test_key", None)
        if not on and self.test_env == 0.0:
            return None
        n = np.arange(BLOCK)
        target = 1.0 if on else 0.0
        env = self.test_env + (target - self.test_env) * np.minimum((n + 1) / BLOCK, 1.0)  # one block fade
        self.test_env = target
        y = np.sin(2 * np.pi * self.TEST_FREQ * (self.test_pos + n) / SR) * self.TEST_LEVEL * env
        self.test_pos = (self.test_pos + BLOCK) % SR
        return y

    def _run(self):
        last_plan = 0.0
        clock = time.monotonic()
        while self.running:
            now = time.monotonic()
            if self.dirty or now - last_plan > self.REPLAN:
                self.dirty, last_plan = False, now
                try:
                    self._plan()
                except Exception as e:  # never let a config surprise stop the engine
                    self.error = f"speaker plan: {e}"
                    print(f"caltreat: {self.error}", file=sys.stderr)
            y = self.audio.render()
            t_block = self.audio.t - BLOCK
            ic = self.intercom.pull(BLOCK, t_block)
            duck_to = self.intercom.duck()
            g = self.duck + (duck_to - self.duck) * np.minimum((np.arange(BLOCK) + 1) / (0.03 * SR), 1.0)
            self.duck = float(g[-1])
            tone = self._tone()
            test_key = getattr(self, "test_key", None)
            test_mask = _mask(self.test[1]) if self.test else (1.0, 1.0)
            for key, p in self.plan.items():
                frames = np.zeros((BLOCK, 2))
                for sp, mask, gain in p["outs"]:
                    sig = y * gain * (g if sp == "inside" else 1.0)
                    if sp == "inside" and ic is not None:
                        sig = sig + ic
                    frames[:, 0] += sig * mask[0]
                    frames[:, 1] += sig * mask[1]
                if tone is not None and key == test_key:
                    frames[:, 0] += tone * test_mask[0]
                    frames[:, 1] += tone * test_mask[1]
                dev = self.devices.get(key)
                if dev:
                    dev.push(frames, t_block)
            # pace by the inside speaker's card (lowest latency for the intercom), else the jack, else any
            inside = self.status.get("inside", {}).get("key")
            master = self.devices.get(inside) or self.devices.get(hw.JACK) or next(iter(self.devices.values()), None)
            if master is not None and master.ok:
                master.wait_room(0.1)
                clock = time.monotonic()
            else:  # no working card: keep time on the clock
                clock += BLOCK / SR
                delay = clock - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.2:
                    clock = time.monotonic()


def _mask(ch):
    return {"left": (1.0, 0.0), "right": (0.0, 1.0)}.get(ch, (1.0, 1.0))


class Io:
    """Lights: every output (the relay shield's 4 channels, each board's GP0-GP7) is mapped in the config to a
    light function, or to nothing (then it is switched by hand from the settings page). Functions: manual ones
    (headlights, markers, interior, your own) are off / on / flash from the engineer page; doors, crossing, horn and
    bell ones follow the train. Two outputs of a function with different phases (alt) alternate when it flashes, as
    ditch lights and crossing flashers do. With `horn_flash` the headlights flash while the horn sounds and for a few
    seconds after. Relays are driven with `pinctrl`, so no GPIO libraries are needed (the user must be in the `gpio`
    group); boards through their `RemoteBoard`."""

    PINS = (4, 22, 6, 26)  # BCM GPIO for relay channels 1-4 (J2-J5 on the shield)
    ACTIVE_HIGH = True  # the shield energizes a relay when its GPIO is high
    FLASH_HALF = 0.5  # s per side: 60 flashes/min each
    HORN_HOLD = 5.0  # s the headlights keep flashing after the horn stops
    MODES = ("off", "on", "flash")

    def __init__(self, audio, cfg, finder, seq_status=lambda: {}):
        self.audio, self.cfg, self.finder, self.seq_status = audio, cfg, finder, seq_status
        self.modes = {}  # manual function id -> off / on / flash
        self.manual = {}  # output key -> bool, for unmapped outputs: ("relay", i) or (chip ID, i)
        self.force = set()  # output keys forced on from the settings page (test)
        self.relay_state = [False] * RELAY_CHANNELS  # relay channels as last written
        self.fn_state = {}  # function id -> (phase A level, phase B level), as last computed
        self.last_horn = -1e9
        self.error = None
        self.running = False
        self.thread = None

    def mode(self, fid):
        return self.modes.get(fid, "off")

    def cycle(self, fid):
        f = self.cfg.function(fid)
        if f and f["kind"] == "manual":
            self.modes[fid] = self.MODES[(self.MODES.index(self.mode(fid)) + 1) % len(self.MODES)]

    def set_mode(self, fid, mode):
        if mode in self.MODES:
            self.modes[fid] = mode

    def toggle_output(self, key):
        """Settings page: an unmapped output toggles; a mapped one is forced on (a test) until tapped again."""
        if self.mapped(key):
            self.force.symmetric_difference_update({key})
        else:
            self.manual[key] = not self.manual.get(key, False)

    def all_off(self, uid):
        for i in range(REMOTE_PINS):
            self.manual[(uid, i)] = False
            self.force.discard((uid, i))

    def mapped(self, key):
        return self.output_cfg(key).get("fn") is not None

    def output_cfg(self, key):
        src, i = key
        if src == "relay":
            return self.cfg.data["relay"][i]
        board = self.cfg.data["boards"].get(src)
        return board["outputs"][i] if board else {"fn": None, "alt": False}

    def functions_now(self, now):
        """{function id: (phase A on, phase B on)} for this moment."""
        a = self.audio
        if a.horn_on:
            self.last_horn = now
        phase = int(now / self.FLASH_HALF) % 2 == 0
        seq = self.seq_status()
        out = {}
        for f in self.cfg.data["functions"]:
            kind, fid = f["kind"], f["id"]
            if kind == "manual":
                mode = self.mode(fid)
                if fid == "head" and mode == "on" and self.cfg.data["horn_flash"] and now - self.last_horn < self.HORN_HOLD:
                    mode = "flash"
            elif kind == "doors":
                mode = "off" if a.doors == "closed" else "on" if a.doors == "open" else "flash"
            elif kind == "crossing":
                crossing = a.busy_pattern == "crossing" or seq.get("phase") == "Crossing"
                mode = "flash" if crossing else "off"
            elif kind == "horn":
                mode = "on" if a.horn_on else "off"
            elif kind == "bell":
                mode = "on" if a.bell.on else "off"
            else:
                mode = "off"
            out[fid] = {"off": (False, False), "on": (True, True), "flash": (phase, not phase)}[mode]
        return out

    def level(self, key, fns):
        if key in self.force:
            return True
        o = self.output_cfg(key)
        if o.get("fn") is None:
            return self.manual.get(key, False)
        a, b = fns.get(o["fn"], (False, False))
        return b if o.get("alt") else a

    def desired(self, now):
        """(relay levels, {chip ID: [levels]}) for this moment."""
        fns = self.fn_state = self.functions_now(now)
        relay = [self.level(("relay", i), fns) for i in range(RELAY_CHANNELS)]
        boards = {b.uid: [self.level((b.uid, i), fns) for i in range(REMOTE_PINS)] for b in self.finder.found}
        return relay, boards

    def _pinctrl(self, pins, *opts):
        if hw.FAKE:  # no Pi: the relays are only shown on the page
            return
        r = subprocess.run(["pinctrl", "set", ",".join(map(str, pins)), *opts], capture_output=True, text=True)
        if r.returncode:
            raise OSError(r.stderr.strip() or f"pinctrl exited with {r.returncode}")

    def _write(self, want, force=False):
        changed = [(p, w) for p, w, s in zip(self.PINS, want, self.relay_state) if force or w != s]
        self.relay_state = list(want)
        for level in (True, False):
            pins = [p for p, w in changed if w == level]
            if pins:
                self._pinctrl(pins, "op", "dh" if level == self.ACTIVE_HIGH else "dl")

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, name="caltreat-lights", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        try:
            self._write([False] * RELAY_CHANNELS, force=True)
        except OSError:
            pass

    def step(self, write_relays=True, force=False):
        relay, boards = self.desired(time.monotonic())
        for b in self.finder.found:
            b.set_want(boards.get(b.uid, [False] * REMOTE_PINS))
        if write_relays:
            self._write(relay, force)

    def _run(self):
        force, retry_at = True, 0.0
        while self.running:
            now = time.monotonic()
            try:
                self.step(now >= retry_at, force)  # the boards keep updating while the relays are failing
                if now >= retry_at:
                    force, self.error = False, None
            except OSError as e:
                if self.error is None:
                    print(f"caltreat: relay output failed: {e}", file=sys.stderr)
                self.error, force, retry_at = str(e) or type(e).__name__, True, now + 2.0
            time.sleep(0.05)


class ModbusError(OSError):
    pass


class ModbusTCP:
    """Just enough of a Modbus TCP client for the lighting controller boards (function codes 4, 15, 16 and 43), on
    a plain socket so no Modbus package is needed. Any error means the connection should be dropped."""

    def __init__(self, host, port=None, timeout=1.0):
        port = REMOTE_PORT if port is None else port
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.tid = 0

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed by the board")
            buf += chunk
        return buf

    def _request(self, pdu):
        self.tid = (self.tid + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, 1) + pdu)
        tid, _, length, _ = struct.unpack(">HHHB", self._recv(7))
        body = self._recv(length - 1)
        if tid != self.tid:
            raise ModbusError(f"reply to transaction {tid}, expected {self.tid}")
        if body[0] == pdu[0] | 0x80:
            raise ModbusError(f"function {pdu[0]}: exception {body[1]}")
        if body[0] != pdu[0]:
            raise ModbusError(f"function {pdu[0]}: unexpected reply {body[0]}")
        return body

    def read_input(self, address, count=1):
        body = self._request(struct.pack(">BHH", 4, address, count))
        return list(struct.unpack(f">{count}H", body[2:2 + 2 * count]))

    def write_registers(self, address, values):
        n = len(values)
        self._request(struct.pack(">BHHB", 16, address, n, 2 * n) + struct.pack(f">{n}H", *values))

    def write_coils(self, address, bits):
        n, packed = len(bits), sum(1 << i for i, b in enumerate(bits) if b)
        nbytes = (n + 7) // 8
        self._request(struct.pack(">BHHB", 15, address, n, nbytes) + packed.to_bytes(nbytes, "little"))

    def device_id(self):
        """Basic device identification (function 43, MEI type 14): {object id: string}."""
        body = self._request(struct.pack(">BBBB", 43, 14, 1, 0))
        objects, pos = {}, 7  # after function, MEI type, read code, conformity, more, next id, object count
        for _ in range(body[6]):
            oid, n = body[pos], body[pos + 1]
            objects[oid] = body[pos + 2:pos + 2 + n].decode(errors="replace")
            pos += 2 + n
        return objects


class RemoteBoard:
    """One CalTreat lighting controller (Waveshare RP2350-ETH running caltreat_lighting_controller, Modbus TCP on
    port 502). Keeps one connection open from a background thread, sets GP0-GP7 as outputs, sends on/off changes
    and polls the board's flags often enough to feed its heartbeat (default 3 s). If the board fell back to its
    failsafe defaults (boot, heartbeat timeout or a dropped connection), the pins are set up again with the
    current levels. When this program stops, the connection closes and the board turns its pins back off.
    The board is identified by its chip ID; `host` is wherever `BoardFinder` last saw it."""

    POLL = 0.5  # s between status reads; well inside the board's heartbeat
    RETRY = 2.0  # s between connection attempts
    # register map (src/regmap.h)
    IR_UID = 3  # 8 bytes, two per register
    IR_FLAGS, IR_FLAG_FAILSAFE = 10, 0x0002
    IR_MAC = 16  # 6 bytes
    HR_PIN_BASE, HR_PIN_STRIDE = 1000, 16
    FUNC_OUTPUT, PULL_NONE, DRIVE_4MA, FLAG_SCHMITT, FLAG_LEVEL_HIGH = 2, 0, 1, 0x02, 0x10

    @classmethod
    def read_uid(cls, conn):
        return struct.pack(">4H", *conn.read_input(cls.IR_UID, 4)).hex().upper()

    def __init__(self, name, uid, host):
        self.name = name
        self.uid = uid
        self.host = host
        self.want = [False] * REMOTE_PINS
        self.mac = None
        self.connected = False
        self.error = None
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.running = False
        self.thread = None

    def set_want(self, levels):
        """Levels for GP0-GP7 (from `Io`); sent at once if they changed."""
        levels = [bool(x) for x in levels]
        with self.lock:
            if levels == self.want:
                return
            self.want = levels
        self.wake.set()

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, name=f"caltreat-board-{self.name}", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=3)

    def _configure(self, conn, levels):
        """Make GP0-GP7 outputs at the given levels (one pin block per write)."""
        for gpio, level in enumerate(levels):
            flags = self.FLAG_SCHMITT | (self.FLAG_LEVEL_HIGH if level else 0)
            conn.write_registers(self.HR_PIN_BASE + self.HR_PIN_STRIDE * gpio,
                                 [self.FUNC_OUTPUT, self.PULL_NONE, self.DRIVE_4MA, flags, 0, 0, 200])

    def _session(self):
        conn = ModbusTCP(self.host)
        try:
            uid = self.read_uid(conn)
            if uid != self.uid:  # its address went to another board; wait for BoardFinder to see it again
                raise ModbusError(f"found board {uid} here instead")
            if self.mac is None:
                try:
                    self.mac = ":".join(f"{b:02X}" for b in struct.pack(">3H", *conn.read_input(self.IR_MAC, 3)))
                except ModbusError:
                    self.mac = ""
            with self.lock:
                sent = list(self.want)
            self._configure(conn, sent)
            self.connected, self.error = True, None
            while self.running:
                self.wake.wait(self.POLL)
                self.wake.clear()
                with self.lock:
                    want = list(self.want)
                if want != sent:
                    conn.write_coils(0, want)
                    sent = want
                if conn.read_input(self.IR_FLAGS)[0] & self.IR_FLAG_FAILSAFE:
                    self._configure(conn, want)  # the pins went back to their defaults; set them up again
                    sent = want
        finally:
            self.connected = False
            conn.close()

    def _run(self):
        while self.running:
            try:
                self._session()
            except OSError as e:  # includes timeouts, refused connections and Modbus exceptions
                if self.running:
                    if self.error is None:
                        print(f"caltreat: board {self.name} ({self.host}): {e}", file=sys.stderr)
                    self.error = str(e) or type(e).__name__
                    time.sleep(self.RETRY)


class BoardFinder:
    """Finds the lighting controllers on eth0: every address in the DHCP range that accepts a Modbus TCP connection
    and identifies as a lighting controller is a board, known by its chip ID. Starts a `RemoteBoard` for each new
    one, and points a known one at its new address if it moved. Addresses of connected boards are skipped, as a
    board serves only one client at a time."""

    SCAN = 5.0  # s between scans while a known board is offline or none has been found
    IDLE_SCAN = 30.0  # s between scans (for newly plugged-in boards) while all known boards are connected
    PROBE_TIMEOUT = 0.5  # s; unused addresses only fail after ARP gives up, so don't wait for that
    PRODUCTS = ("lighting-controller", "lighting-manager")  # device ID product codes; firmware 0.4.x used the latter

    def __init__(self, cfg):
        self.cfg = cfg
        self.boards = {}  # chip ID -> RemoteBoard
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.running = False
        self.thread = None

    @property
    def found(self):
        """The boards found so far, by name."""
        with self.lock:
            return sorted(self.boards.values(), key=lambda b: b.name)

    def _name(self, uid):
        """The board's name from the config; a new board gets the next free letter (saved)."""
        boards = self.cfg.data["boards"]
        if uid not in boards:
            used = {b["name"] for b in boards.values()}
            name = next((c for c in map(chr, range(ord("A"), ord("Z") + 1)) if c not in used), uid[-6:])
            boards[uid] = board_defaults(name)
            self.cfg.save()
        return boards[uid]["name"]

    def rename(self):
        """After a config change: board names may have changed."""
        with self.lock:
            for uid, b in self.boards.items():
                if uid in self.cfg.data["boards"]:
                    b.name = self.cfg.data["boards"][uid]["name"]

    def _probe(self, host):
        """The chip ID of the lighting controller at host, or None."""
        try:
            conn = ModbusTCP(host, timeout=self.PROBE_TIMEOUT)
        except OSError:
            return None
        try:
            if conn.device_id().get(0x01) not in self.PRODUCTS:
                return None
            return RemoteBoard.read_uid(conn)
        except (OSError, IndexError, struct.error):  # anything that doesn't answer like a board
            return None
        finally:
            conn.close()

    def scan(self):
        with self.lock:
            busy = {b.host for b in self.boards.values() if b.connected}
        hosts = [f"{REMOTE_NET}{i}" for i in REMOTE_HOSTS if f"{REMOTE_NET}{i}" not in busy]
        with ThreadPoolExecutor(max_workers=64) as pool:
            found = list(zip(hosts, pool.map(self._probe, hosts)))
        for host, uid in found:
            if uid is None:
                continue
            name = self._name(uid)  # may save the config, whose listeners take self.lock: not inside it
            with self.lock:
                board = self.boards.get(uid)
                if board is None:
                    board = self.boards[uid] = RemoteBoard(name, uid, host)
                    print(f"caltreat: found board {board.name} ({uid}) at {host}", file=sys.stderr)
                    if self.running:
                        board.start()
                elif not board.connected and board.host != host:
                    print(f"caltreat: board {board.name} moved to {host}", file=sys.stderr)
                    board.host = host

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, name="caltreat-board-finder", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
        for board in self.found:
            board.stop()

    def _run(self):
        while self.running:
            self.scan()
            boards = self.found
            idle = boards and all(b.connected for b in boards)
            self.wake.wait(self.IDLE_SCAN if idle else self.SCAN)




# ---------- the app's one engine, shared by every browser ----------
cfg = Config.load()
if cfg.error:
    print(f"caltreat: {cfg.error}", file=sys.stderr)
audio = AudioOut()
intercom = Intercom(lambda: cfg.data["intercom"])
intercom.keep_marks = bool(hw.FAKE_DIR)  # latency marks for the tests
router = Router(audio, intercom, cfg)
finder = BoardFinder(cfg)
seq_lock = threading.RLock()


def seq_send(m):
    """The sequencer's messages -> the engine."""
    t = m["t"]
    if t == "bell":
        audio.set_bell(m["on"])
    elif t == "throttle":
        audio.set_throttle(m["v"])
    elif t == "stop":
        audio.stop_train()
    elif t == "hiss":
        audio.air()
    elif t == "doors":
        audio.doors_cmd(m["v"])
    elif t == "pattern":
        key = m.get("key") or ("crossing" if m["seq"] == signal_pattern("crossing", audio.timing) else "seq")
        audio.play_seq(m["seq"], key)
    elif t == "stopPattern":
        audio.stop_pattern()


sequencer = Sequencer(seq_send)


def seq_status():
    with seq_lock:
        return sequencer.status()


io = Io(audio, cfg, finder, seq_status)


def apply_config():
    audio.apply_mix(cfg.data["mix"])
    audio.set_timing(cfg.data["timing"])
    finder.rename()


def apply_volumes():
    """The ALSA card volumes kept in the config (unset ones are left alone)."""
    usb = hw.usb_outputs()
    for dev, pct in cfg.data["volumes"].items():
        card, _ = hw.resolve(dev, usb)
        if card:
            hw.set_volume(pct, card, hw.mixer_control(card))


cfg.on_change(apply_config)
apply_config()


class SeqDriver:
    """Ticks the sequencer every 50 ms with the engine's state (speed, doors)."""

    def __init__(self):
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._run, name="caltreat-sequencer", daemon=True).start()

    def stop(self):
        self.running = False

    def _run(self):
        while self.running:
            with seq_lock:
                sequencer.tick(time.monotonic(), audio.state())
            time.sleep(0.05)


seq_driver = SeqDriver()
app.on_startup(apply_volumes)
app.on_startup(router.start)
app.on_startup(io.start)
app.on_startup(finder.start)
app.on_startup(seq_driver.start)
app.on_shutdown(router.stop)
app.on_shutdown(io.stop)
app.on_shutdown(finder.stop)
app.on_shutdown(seq_driver.stop)
app.add_static_files("/static", STATIC)


# ---------- intercom WebSocket ----------
@app.websocket("/intercom/ws")
async def intercom_ws(ws: WebSocket):
    """One talker: {"ok": true} then binary frames (see intercom.py) until the page closes it; {"busy": true} if
    someone else talks; {"stop": "timeout" | "stale"} when the server ends it."""
    await ws.accept()
    token = uuid.uuid4().hex
    if not intercom.claim(token, ws.query_params.get("c")):
        await ws.send_text(json.dumps({"busy": True}))
        await ws.close()
        return
    await ws.send_text(json.dumps({"ok": True}))

    async def watchdog():
        while intercom.talker == token:
            await asyncio.sleep(0.25)
            why = intercom.expired()
            if why and intercom.talker == token:
                intercom.release(token)
                with contextlib.suppress(Exception):
                    await ws.send_text(json.dumps({"stop": why}))
                    await ws.close()
                return

    dog = asyncio.create_task(watchdog())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") and not intercom.feed(token, msg["bytes"]):
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        intercom.release(token)
        dog.cancel()


@app.get("/api/intercom")
def intercom_api():
    """Intercom state (and, in fake-audio test runs, the latency marks)."""
    out = {"talking": intercom.talking, "stats": intercom.stats, "duck": router.duck}
    if hw.FAKE_DIR:
        out["marks"] = intercom.marks[-2000:]
        out["t0"] = intercom.t0
        out["devices"] = {k: {"written": list(d.written), "underruns": d.underruns, "dropped": d.dropped}
                          for k, d in router.devices.items()}
    return out


# ---------- HTTPS (the microphone needs a secure page) ----------
CERT = cfg.folder / "https-cert.pem"
KEY = cfg.folder / "https-key.pem"


def ensure_cert():
    """A self-signed certificate for this Pi, made on first start (kept in the config folder, renewed 30 days
    before it expires). Returns True if HTTPS can run."""
    try:
        if CERT.exists() and KEY.exists() and subprocess.run(
                ["openssl", "x509", "-checkend", str(30 * 86400), "-noout", "-in", str(CERT)],
                capture_output=True).returncode == 0:
            return True
        host = socket.gethostname().split(".")[0] or "caltreat"
        names = [f"DNS:{host}.local", f"DNS:{host}", "DNS:localhost", "IP:127.0.0.1", "IP:192.168.50.1"]
        try:
            ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
        except OSError:
            ips = []
        names += [f"IP:{ip}" for ip in ips if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip) and f"IP:{ip}" not in names]
        cfg.folder.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "825",
                            "-keyout", str(KEY), "-out", str(CERT), "-subj", f"/CN={host}.local/O=Caltreat",
                            "-addext", "subjectAltName=" + ",".join(names),
                            "-addext", "extendedKeyUsage=serverAuth"], capture_output=True, text=True)
        if r.returncode:
            print(f"caltreat: could not make the HTTPS certificate: {r.stderr.strip()}", file=sys.stderr)
            return False
        KEY.chmod(0o600)
        print(f"caltreat: made a self-signed HTTPS certificate for {', '.join(names)}", file=sys.stderr)
        return True
    except OSError as e:
        print(f"caltreat: no HTTPS ({e})", file=sys.stderr)
        return False


class _QuietServer(uvicorn.Server):
    """The HTTPS server runs beside NiceGUI's in the same event loop; NiceGUI's server handles the signals."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


https = {"server": None, "on": False}


async def start_https():
    if HTTPS_PORT <= 0 or not ensure_cert():
        return
    config = uvicorn.Config(app, host="0.0.0.0", port=HTTPS_PORT, ssl_certfile=str(CERT), ssl_keyfile=str(KEY),
                            lifespan="off", log_level="warning", ws="wsproto")
    server = https["server"] = _QuietServer(config)
    https["on"] = True

    async def serve():
        try:
            await server.serve()
        except (SystemExit, OSError) as e:  # e.g. the port is taken: plain http keeps working
            print(f"caltreat: HTTPS on port {HTTPS_PORT} failed ({e})", file=sys.stderr)
        https["on"] = False

    https["task"] = asyncio.get_running_loop().create_task(serve())


def stop_https():
    if https["server"]:
        https["server"].should_exit = True


app.on_startup(start_https)
app.on_shutdown(stop_https)


def https_url(client):
    try:
        host = client.request.url.hostname or "caltreat.local"
    except AttributeError:
        host = "caltreat.local"
    return f"https://{host}:{HTTPS_PORT}/" if https["on"] else ""


# ---------- pages ----------
HEAD = """<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="stylesheet" href="/static/cab.css">"""
ICON_GEAR = ('<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3M12 18.5v3'
             'M21.5 12h-3M5.5 12h-3M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1M18.7 18.7l-2.1-2.1M7.4 7.4L5.3 5.3"/></svg>')
ICON_BACK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>'
ICON_MIC = ('<svg class="mic" viewBox="0 0 40 40" aria-hidden="true"><rect x="14" y="4" width="12" height="20" rx="6"/>'
            '<path d="M8 19a12 12 0 0 0 24 0M20 31v6M14 37h12"/></svg>')
RING = ('<svg class="pring" viewBox="0 0 64 64" aria-hidden="true"><circle class="rt" cx="32" cy="32" r="29"/>'
        '<circle class="rp" cx="32" cy="32" r="29"/></svg>')
SIG_TXT = {"alarm": "o o o …"}


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def engineer_html():
    pats = "".join(
        f'<button class="pat" data-k="{k}" title="{_esc(rule)} (key {i + 1})" aria-label="{_esc(label)} horn signal">'
        f'<code>{SIG_TXT.get(k, sig)}</code><b>{_esc(label)}</b></button>'
        for i, (k, label, sig, rule) in enumerate(HORN_SIGNALS))
    lights = "".join(
        f'<button class="lt{" red" if f["id"] == "markers" else ""}" id="lt_{f["id"]}" data-f="{f["id"]}" '
        f'title="{_esc(f["name"])}: tap off / on / flash{" (L)" if f["id"] == "head" else " (M)" if f["id"] == "markers" else ""}">'
        f'<i aria-hidden="true">{_esc(f.get("symbol") or "●")}</i><b>{_esc(f["name"])}</b><small></small></button>'
        for f in cfg.data["functions"] if f["kind"] == "manual")
    return f"""
<main class="desk">
  <header>
    <div class="mark"><b>KISS</b><span>Caltreat · cab</span></div>
    <div class="hd-right"><div class="chip" id="status">…</div>
      <a class="iconbtn" href="/settings" title="Settings: mix, speakers, lights, intercom, backup" aria-label="Settings">{ICON_GEAR}</a></div>
  </header>
  <section class="lcd" aria-label="Cab display">
    <div class="k">Speed</div><div class="k">Inverter</div>
    <div class="lamps">
      <div class="lamp horn" id="lampHorn"><i></i>Horn</div>
      <div class="lamp bell" id="lampBell"><i></i>Bell</div>
      <div class="lamp door" id="lampDoor"><i></i>Doors</div>
    </div>
    <div class="v"><span id="mph">0</span><small>mph</small></div>
    <div class="v"><span id="hz">0</span><small>Hz</small></div>
  </section>
  <section class="seqs" aria-label="Sequences">
    <button class="sq" id="sqDepart" title="Depart (G): doors close (warning tone), closed and locked, brake release air, bell on before moving (GCOR 5.8.1), horn – – = release brakes, proceed (GCOR 5.8.2(3); off in Settings), power to cruise, bell off clear of the platform. Tap again to cancel." aria-label="Depart sequence">
      {RING}<svg class="ic" viewBox="0 0 40 40" aria-hidden="true"><path d="M4 31h32M8 25h14l6-8H8z"/><path d="M27 10l7 7-7 7M34 17H20"/></svg><b>Depart</b></button>
    <button class="sq" id="sqArrive" title="Arrive (R): bell on approaching the station and horn o (Caltrain practice; no rule requires either at stations, 49 CFR 222.23(d)), power off, brake to a stop, bell off, doors open. Tap again to cancel." aria-label="Arrive sequence">
      {RING}<svg class="ic" viewBox="0 0 40 40" aria-hidden="true"><path d="M4 31h32M18 25h14V17H24z"/><path d="M4 11h10M10 6l5 5-5 5M34 6v12"/></svg><b>Arrive</b></button>
    <button class="sq" id="sqCrossing" title="Grade crossing (X): bell on, horn – – o – (49 CFR 222.21, GCOR 5.8.2(7)), started 15-20 s before the crossing (Real: 17 s), last long prolonged until the lead car occupies it; bell on until then (GCOR 5.8.1). Works while moving. Tap again to cancel." aria-label="Grade crossing sequence">
      {RING}<svg class="ic" viewBox="0 0 40 40" aria-hidden="true"><path d="M7 7l26 26M33 7L7 33M20 24v13"/></svg><b>Crossing</b></button>
  </section>
  <div class="seqbar">
    <button class="auto" id="sqAuto" aria-pressed="false" title="Auto run (U): Depart, cruise with two grade crossings, Arrive, dwell at the platform, repeat until tapped again" aria-label="Auto run">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5"/></svg></button>
    <div class="prog" aria-live="polite"><i id="seqFill"></i><span id="seqStep" title="Current step of the running sequence"></span></div>
    <span class="seg" role="group" aria-label="Sequence timing" id="timing">
      <button type="button" data-m="costume" title="Costume timing: short blasts, quick departure (14 s to full power)">Show</button><button type="button" data-m="real" title="Real timing: blast lengths, dwell and acceleration as on the railroad (about 60 s to full power)">Real</button>
    </span>
    <button class="x" id="seqCancel" title="Stop the sequence (Esc). The train keeps its speed; the sequence's bell and horn stop." aria-label="Stop sequence" disabled>&#x2715;</button>
  </div>
  <section class="main">
    <button class="pad bell" id="bell" aria-pressed="false" title="Bell (B): tap on / off, 2.5 strikes/s" aria-label="Bell">
      <svg class="pi" viewBox="0 0 40 40" aria-hidden="true"><path d="M20 6c-6 0-9 5-9 11v7l-3 5h24l-3-5v-7c0-6-3-11-9-11zM16 32a4 4 0 0 0 8 0"/></svg><b>Bell</b></button>
    <button class="pad door" id="door" aria-pressed="false" aria-label="Doors" title="Doors (D): tap to open, tap again to close (warning tone 3 s, or as long as you hold), leaves slide, closed and locked. A tap while they move reverses them.">
      <svg class="dicon" viewBox="0 0 56 44" aria-hidden="true"><rect class="f" x="2" y="2" width="52" height="40" rx="3"/><rect class="l" id="leafL" x="5" y="5" width="22.5" height="34" rx="1.5"/><rect class="l" id="leafR" x="28.5" y="5" width="22.5" height="34" rx="1.5"/></svg><b>Doors</b></button>
    <button class="hornbtn" id="horn" aria-label="Horn (hold)" title="Horn (Space / H): hold to sound"><b>HORN</b><span>hold</span></button>
  </section>
  <button class="talk" id="talk" aria-label="Hold to talk on the inside speakers"><i class="lvl"></i>{ICON_MIC}<b>Talk</b><span>hold</span><em class="lock" aria-hidden="true">&#x1F512; https</em></button>
  <section class="ctl power">
    <h2><label for="throttle" title="Power controller (arrow keys, 0 = off)">&#x25B2; Power</label><output id="thrOut">0 %</output></h2>
    <input type="range" id="throttle" min="0" max="100" step="1" value="0" aria-label="Throttle">
    <div class="notches" aria-hidden="true"><span>OFF</span><span>25</span><span>50</span><span>75</span><span>FULL</span></div>
    <div class="brk">
      <button class="pad" id="air" title="Air (A): brake release hiss" aria-label="Air"><i aria-hidden="true">&#x224B;</i><b>Air</b></button>
      <button class="pad" id="stop" title="Stop (S): power off and brake to a halt: squeal near the end, then the air. Standing: the squeal." aria-label="Stop"><i aria-hidden="true">&#x25A0;</i><b>Stop</b></button>
    </div>
  </section>
  <section class="ctl">
    <h2 title="GCOR 5.8.2 horn signals: o short, – long. Tap one (keys 1-9); hover for the rule">Horn signals</h2>
    <div class="patterns" id="patterns">{pats}</div>
  </section>
  <section class="ctl">
    <h2 title="Lights: tap to cycle off / on / flash. What each light drives is set in Settings">Lights</h2>
    <div class="lights">{lights}</div>
  </section>
  <footer class="foot">
    <p class="keys">Keys: <kbd>G</kbd> depart · <kbd>R</kbd> arrive · <kbd>X</kbd> crossing · <kbd>U</kbd> auto run · <kbd>Esc</kbd> stop sequence · <kbd>Space</kbd> horn (hold) · <kbd>T</kbd> talk (hold) · <kbd>B</kbd> bell · <kbd>D</kbd> doors · <kbd>A</kbd> air · <kbd>S</kbd> stop · <kbd>↑</kbd><kbd>↓</kbd> power · <kbd>0</kbd> power off · <kbd>1</kbd>–<kbd>9</kbd> horn signals · <kbd>L</kbd> headlights · <kbd>M</kbd> markers</p>
  </footer>
</main>"""


def errors():
    errs = [f"Speaker {k}: {d.error}" for k, d in router.devices.items() if d.error]
    errs += [f"Relays: {io.error}" if io.error else "", router.error or "", cfg.error or ""]
    errs += [f"Board {b.name} ({b.host}): {b.error}" for b in finder.found if b.error and not b.connected]
    return " · ".join(e for e in errs if e)


def engine_state(who):
    q = seq_status()
    talk = None
    if intercom.talking:
        talk = "me" if intercom.who == who else "busy"
    return {
        "mph": round(audio.motor.speed * 79), "hz": round(audio.motor.speed * MOTOR["a_max"]),
        "horn": audio.horn_on, "bell": audio.bell.on, "doors": audio.doors, "doorPos": round(audio.door_pos, 3),
        "thr": round(audio.motor.throttle * 100), "timing": audio.timing, "pattern": audio.busy_pattern,
        "seq": {"active": q["active"], "step": q["step"], "progress": round(q["progress"], 3),
                "index": q.get("index")},
        "lights": {f["id"]: io.mode(f["id"]) for f in cfg.data["functions"] if f["kind"] == "manual"},
        "talk": talk, "err": errors(),
    }


def set_timing(mode):
    if mode in ("costume", "real") and cfg.data["timing"] != mode:
        cfg.data["timing"] = mode
        cfg.save()


def act(who, a, v):
    """One press on the engineer page."""
    with seq_lock:
        if a == "horn":
            if v:
                sequencer.manual("horn")
            audio.hold(who, bool(v))
        elif a == "bell":
            sequencer.manual("bell")
            audio.toggle_bell()
        elif a == "door":
            if v:
                sequencer.manual("doors")
                audio.door_press(who)
            else:
                audio.door_release(who)
        elif a == "air":
            sequencer.manual("air")
            audio.air()
        elif a == "stop":
            sequencer.manual("brake")
            audio.stop_train()
        elif a == "throttle":
            sequencer.manual("power")
            audio.set_throttle(float(v) / 100)
        elif a == "signal":
            if v == "show":
                sequencer.manual("horn")
                audio.play_seq(SHOW_PATTERN, "show")
            elif v in dict((k, 1) for k, *_ in SIGNALS):
                sequencer.signal(v, audio.timing)
        elif a == "seq":
            if sequencer.active == v:
                sequencer.cancel()
            elif v in ("depart", "arrive", "crossing", "auto"):
                sequencer.start(v, audio.timing, depart_horn=cfg.data["depart_horn"])
        elif a == "cancel":
            sequencer.cancel()
        elif a == "timing":
            set_timing(v)
        elif a == "light":
            io.cycle(v)


def page_head(title):
    ui.page_title(title)
    ui.add_head_html(HEAD)
    ui.query("body").classes("cab")


@ui.page("/")
async def engineer(client):
    page_head("Caltreat")
    who = client.id
    ui.add_head_html('<script src="/static/cab.js"></script>')
    ui.html(engineer_html(), sanitize=False).classes("w-full")
    ui.on("ct", lambda e: act(who, (e.args or {}).get("a"), (e.args or {}).get("v")))
    client.on_disconnect(lambda: audio.release(who))
    sent = {}

    def push():
        st = engine_state(who)
        changed = {k: v for k, v in st.items() if sent.get(k) != v}
        if changed:
            sent.update(changed)
            ui.run_javascript(f"window.ct && ct.update({json.dumps(changed)})")

    await client.connected()
    raw = "raw=1" in str(getattr(client.request, "url", ""))  # tests: microphone without noise suppression / AGC
    ui.run_javascript(f"ct.init({json.dumps({'client': who, 'https': https_url(client), 'raw': raw})})")
    push()
    ui.timer(0.1, push)


# ---------- settings page ----------
def el(tag, cls="", text=None, title=None, **attrs):
    e = ui.element(tag).classes(cls)
    if title:
        e.props(f'title="{_esc(title)}"')
    for k, v in attrs.items():
        e.props(f'{k.replace("_", "-")}="{_esc(v)}"')
    if text is not None:
        with e:
            ui.html(str(text), sanitize=False, tag="span")
    return e


def sbtn(text, on_tap, title=None, cls=""):
    b = el("button", f"sbtn {cls}", text, title, type="button")
    b.on("click", lambda e: on_tap())
    return b


def select(options, value, on_change, title=None, cls="sel"):
    s = el("select", cls, title=title)
    with s:
        for v, label in options:
            o = ui.element("option").props(f'value="{_esc(v)}"' + (" selected" if v == value else ""))
            with o:
                ui.html(_esc(label), sanitize=False, tag="span")
    s.on("change", lambda e: on_change(e.args), js_handler="(e) => emit(e.target.value)")
    return s


def slider(label, lo, hi, value, on_change, title=None, unit="%", step=1):
    with el("label", "lbl", title=title):
        ui.html(_esc(label), sanitize=False, tag="span")
        out = ui.html(f"{value} {unit}", sanitize=False, tag="output")
    r = el("input", "", type="range", min=lo, max=hi, step=step, value=value)
    r.props(f'aria-label="{_esc(label)}"')

    def changed(v):
        v = float(v)
        v = int(v) if float(step).is_integer() else v
        out.set_content(f"{v} {unit}")
        on_change(v)

    r.on("input", lambda e: out.set_content(f"{e.args} {unit}"), js_handler="(e) => emit(e.target.value)")
    r.on("change", lambda e: changed(e.args), js_handler="(e) => emit(e.target.value)")
    return r


def device_options():
    usb = hw.usb_outputs()
    opts = [("none", "— none"), ("jack", "3.5 mm jack"), *[(f"usb#{i + 1}", f"USB #{i + 1} (first by port)" if i == 0 else
                                                           f"USB #{i + 1} (by port order)") for i in range(max(2, len(usb)))]]
    opts += [(f"usb:{p}", f"USB port {p} · {c}") for c, p in usb]
    return opts


CH_LABEL = {"both": "L+R", "left": "L", "right": "R"}
SPK = {"front": ("Front", "Outside, front cab"), "inside": ("Inside", "Passengers + intercom 🎤"),
       "rear": ("Rear", "Outside, back")}
KIND_LABEL = {"manual": "manual (engineer page)", "doors": "doors open / moving", "crossing": "crossing signal",
              "horn": "while the horn sounds", "bell": "while the bell rings"}


def save():
    cfg.save()


@ui.page("/settings")
async def settings(client):
    page_head("Caltreat settings")
    live = []  # (callable) run every 0.5 s to show live state

    def download():
        ui.download.content(cfg.export().encode(), "caltreat-config.json", "application/json")

    async def on_upload(e):
        text = await e.file.text()
        new, errs = parse(text)
        upload.reset()
        if errs:
            with ui.dialog() as d, el("div", "dlg"):
                el("b", "", "✗ Not a Caltreat config")
                with el("ul"):
                    for x in errs[:30]:
                        el("li", "", _esc(x))
                sbtn("OK", d.close)
            d.open()
            return
        changes = diff(cfg.data, new)
        with ui.dialog() as d, el("div", "dlg"):
            el("b", "", f"Restore {_esc(e.file.name)}? {len(changes)} change{'s' if len(changes) != 1 else ''}")
            with el("ul"):
                for x in changes[:200] or ["(nothing changes)"]:
                    el("li", "", _esc(x))
            with el("div", "ctlz"):
                sbtn("✓ Apply", lambda: (cfg.replace(new), apply_volumes(), d.close(), body.refresh()),
                     "Apply and save", "on")
                sbtn("✕", d.close, "Cancel")
        d.open()

    def reset():
        with ui.dialog() as d, el("div", "dlg"):
            el("b", "", "Reset every setting to its default? Board names stay.")
            with el("div", "ctlz"):
                sbtn("⟲ Reset", lambda: (cfg.reset(), d.close(), body.refresh()), "Reset", "warn")
                sbtn("✕", d.close, "Cancel")
        d.open()

    upload = ui.upload(on_upload=on_upload, auto_upload=True, max_file_size=1_000_000).props('accept=".json"') \
        .classes("hidden")

    @ui.refreshable
    def body():
        live.clear()
        d = cfg.data
        with el("main", "desk wide"):
            with el("header"):
                with el("div", "hd-right"):
                    el("a", "iconbtn", ICON_BACK, "Back to the cab", href="/")
                    with el("div", "mark"):
                        el("b", "", "Settings")
                with el("div", "hd-right"):
                    sbtn("⤓", download, "Download all settings (caltreat-config.json) as a backup")
                    sbtn("⤒", lambda: upload.run_method("pickFiles"), "Restore settings from a downloaded file "
                                                                        "(shows what changes first)")
                    sbtn("⟲", reset, "Reset to defaults")

            # --- mix ---
            with el("section", "ctl set"):
                el("h2", "", "Mix", "Levels of each sound, % of the engine's default; the card volumes are under "
                                    "Speakers")
                for k, lab, tip in (("master", "Master", "All train sounds"), ("horn", "Horn", ""),
                                    ("bell", "Bell", ""), ("motor", "Motor whine", ""),
                                    ("doors", "Door tone", ""), ("air", "Air", "Brake release hiss"),
                                    ("brakes", "Brake squeal", "")):
                    slider(lab, 0, 150, d["mix"][k], lambda v, k=k: (d["mix"].__setitem__(k, v), save()), tip or None)
                with el("div", "srow"):
                    el("div", "nm", "Timing", "Show: costume timing (14 s to full power, short blasts). Real: the "
                                              "railroad's (about 60 s, 2.5 s longs)")
                    with el("div", "ctlz"):
                        for m, lab in (("costume", "Show"), ("real", "Real")):
                            sbtn(lab, lambda m=m: (set_timing(m), body.refresh()), None,
                                 "on" if d["timing"] == m else "")
                with el("div", "srow"):
                    el("div", "nm", "Depart", "Depart: – – (release brakes, proceed, GCOR 5.8.2(3)) after the bell, "
                                              "or the bell only (GCOR 5.8.1 requires only the bell)")
                    with el("div", "ctlz"):
                        for v, lab in ((True, "Bell + – –"), (False, "Bell only")):
                            sbtn(lab, lambda v=v: (d.__setitem__("depart_horn", v), save(), body.refresh()), None,
                                 "on" if d["depart_horn"] == v else "")

            # --- speakers ---
            with el("section", "ctl set"):
                el("h2", "", "Speakers", "Which sound card and channel each speaker is on. The train sound plays on "
                                         "all of them; the intercom on Inside. ▶ = 440 Hz test tone")
                with el("div", "rows"):
                    for sp in SPEAKERS:
                        s = d["speakers"][sp]
                        with el("div", "srow"):
                            name, tip = SPK[sp]
                            with el("div", "nm", title=tip):
                                ui.html(name, sanitize=False, tag="span")
                                st = ui.html("", sanitize=False, tag="small")
                            with el("div", "ctlz"):
                                select(device_options(), s["device"],
                                       lambda v, sp=sp: (d["speakers"][sp].__setitem__("device", v), save()),
                                       "Sound card: USB #n = the n-th card by USB port; USB port x.y = that socket")
                                select([(c, CH_LABEL[c]) for c in CHANNELS], s["ch"],
                                       lambda v, sp=sp: (d["speakers"][sp].__setitem__("ch", v), save()),
                                       "Channel", "sel sm")
                                tb = sbtn("▶", lambda sp=sp: test_speaker(sp), "Test tone on this speaker")
                            slider("Level", 0, 100, s["level"],
                                   lambda v, sp=sp: (d["speakers"][sp].__setitem__("level", v), save()))

                            def show(st=st, tb=tb, sp=sp):
                                r = router.status.get(sp, {})
                                ok = r.get("ok")
                                txt = "—" if ok is None else (f"✓ {r.get('card')}" if ok else f"✗ {r.get('desc')}")
                                st.set_content(_esc(txt))
                                st.classes(replace="bad" if ok is False else "")
                                on = router.test is not None and router.test[0] == d["speakers"][sp]["device"] \
                                    and router.test[1] == d["speakers"][sp]["ch"]
                                tb.classes(replace="sbtn on" if on else "sbtn")
                            live.append(show)
                el("h2", "", "Card volume", "The sound cards' own volume (ALSA mixer); kept in the settings")
                usb = hw.usb_outputs()
                for dev, label, card in [("jack", "3.5 mm jack", hw.JACK_CARD),
                                         *[(f"usb:{p}", f"USB {p} · {c}", c) for c, p in usb]]:
                    cur = d["volumes"].get(dev, hw.get_volume(card, hw.mixer_control(card)))

                    def setv(v, dev=dev, card=card):
                        hw.set_volume(v, card, hw.mixer_control(card))
                        d["volumes"][dev] = v
                        save()
                    slider(label, 0, 100, cur, setv)
                el("div", "mapdia", "Raw test: " + " ".join(
                    f"{p}" for _, p in usb) if usb else "No USB sound cards found",
                   "USB sound cards found, by port")
                with el("div", "ctlz"):
                    for dev, label in [("jack", "Jack"), *[(f"usb:{p}", f"USB {p}") for _, p in usb]]:
                        for ch in ("left", "right"):
                            b = sbtn(f"{label} {CH_LABEL[ch]} ▶", lambda dev=dev, ch=ch: raw_test(dev, ch),
                                     f"Test tone on {label}, {ch} channel only")

                            def show_raw(b=b, dev=dev, ch=ch):
                                on = router.test == (dev, ch)
                                b.classes(replace="sbtn on" if on else "sbtn")
                            live.append(show_raw)

            # --- intercom ---
            with el("section", "ctl set"):
                el("h2", "", "Intercom 🎤 → Inside", "Push-to-talk from the engineer page plays on the Inside "
                                                    "speaker(s); the train sound there is ducked while talking")
                ic = d["intercom"]
                slider("Mic gain", -20, 30, ic["mic_gain_db"], lambda v: (ic.__setitem__("mic_gain_db", v), save()),
                       "Boost or cut the phone's microphone. Lower it if the speakers feed back", "dB")
                slider("Output", 0, 100, ic["level"], lambda v: (ic.__setitem__("level", v), save()),
                       "Intercom level on the inside speakers")
                slider("Duck train", -60, 0, ic["duck_db"], lambda v: (ic.__setitem__("duck_db", v), save()),
                       "How much quieter the train sounds get on Inside while someone talks", "dB")
                slider("Max talk", 5, 600, ic["timeout_s"], lambda v: (ic.__setitem__("timeout_s", v), save()),
                       "A held talk button stops after this long (safety)", "s", 5)
                ic_st = ui.html("", sanitize=False, tag="div").classes("mapdia")
                live.append(lambda: ic_st.set_content(
                    _esc(("🎤 talking · " if intercom.talking else "idle · ")
                         + f"buffer {intercom.stats.get('buffer_ms', 0):.0f} ms · frames {intercom.stats.get('frames', 0)}"
                         + f" · lost {intercom.stats.get('lost', 0)} · underruns {intercom.stats.get('underruns', 0)}")))

            # --- lights ---
            fn_opts = [("", "— by hand")] + [(f["id"], f"{f.get('symbol', '')} {f['name']}") for f in d["functions"]]

            def out_row(key, name, sub, ocfg, on_map):
                with el("div", "srow"):
                    with el("div", "nm"):
                        ui.html(_esc(name), sanitize=False, tag="span")
                        ui.html(_esc(sub), sanitize=False, tag="small")
                    with el("div", "ctlz"):
                        lamp = el("i", "lampdot", title="Live: output on")
                        select(fn_opts, ocfg.get("fn") or "", lambda v: on_map("fn", v or None),
                               "Light function driving this output")
                        select([("0", "A"), ("1", "B")], "1" if ocfg.get("alt") else "0",
                               lambda v: on_map("alt", v == "1"),
                               "Phase: A and B outputs of one function alternate when it flashes", "sel sm")
                        tb = sbtn("⏻", lambda: io.toggle_output(key),
                                  "By hand: switch on / off. Mapped: force on as a test (tap again to release)")

                def show():
                    on = (io.relay_state[key[1]] if key[0] == "relay" else
                          next((b.want[key[1]] for b in finder.found if b.uid == key[0]), False))
                    lamp.classes(replace="lampdot on" if on else "lampdot")
                    forced = key in io.force or (not io.mapped(key) and io.manual.get(key))
                    tb.classes(replace="sbtn on" if forced else "sbtn")
                live.append(show)

            def remap(target, field, v):
                target[field] = v
                save()

            with el("section", "ctl set"):
                el("h2", "", "Light functions", "What the engineer page and the sequences switch. Map outputs to "
                                                "them below. ＋ adds your own (switched off / on / flash from the "
                                                "engineer page)")
                with el("div", "rows"):
                    for f in d["functions"]:
                        with el("div", "srow"):
                            el("div", "nm", f"{_esc(f.get('symbol', ''))} {_esc(f['name'])}", KIND_LABEL[f["kind"]])
                            with el("div", "ctlz"):
                                lamp = el("i", "lampdot white", title="Live")
                                outs = [f"R{i + 1}" for i, o in enumerate(d["relay"]) if o["fn"] == f["id"]]
                                outs += [f"{b['name']}:GP{i}" for b in d["boards"].values()
                                         for i, o in enumerate(b["outputs"]) if o["fn"] == f["id"]]
                                el("span", "st", " ".join(outs) or "—", "Outputs mapped to it")
                                if f["kind"] == "manual":
                                    def cyc(fid=f["id"]):
                                        io.cycle(fid)
                                    mb = sbtn(io.mode(f["id"]), cyc, "Off / on / flash (as on the engineer page)")
                                if f["id"] not in BUILTIN_IDS:
                                    sbtn("🗑", lambda fid=f["id"]: delete_fn(fid), f"Delete {f['name']} (its outputs "
                                                                                    "go back to by hand)", "warn")

                        def show_fn(lamp=lamp, fid=f["id"], mb=mb if f["kind"] == "manual" else None):
                            a, b = io.fn_state.get(fid, (False, False))
                            lamp.classes(replace="lampdot white on" if (a or b) else "lampdot white")
                            if mb is not None:
                                mb.clear()
                                with mb:
                                    ui.html(io.mode(fid), sanitize=False, tag="span")
                        live.append(show_fn)
                with el("div", "ctlz"):
                    new_name = el("input", "txt", type="text", placeholder="New light, e.g. Smoke",
                                  maxlength="40")
                    new_name.on("change", lambda e: add_fn(e.args), js_handler="(e) => emit(e.target.value)")
                    sbtn("＋", lambda: ui.run_javascript(
                        f"getHtmlElement({new_name.id}).dispatchEvent(new Event('change'))"), "Add a light function")
                with el("div", "srow"):
                    el("div", "nm", "Horn ☀", "Headlights flash (alternate A/B) while the horn sounds and 5 s after, "
                                             "as ditch lights at crossings")
                    with el("div", "ctlz"):
                        for v, lab in ((True, "Flash with horn"), (False, "Steady")):
                            sbtn(lab, lambda v=v: (d.__setitem__("horn_flash", v), save(), body.refresh()), None,
                                 "on" if d["horn_flash"] == v else "")

            with el("section", "ctl set"):
                el("h2", "", "Relay shield", "The Pi's 4-channel relay shield (BCM GPIO 4, 22, 6, 26)")
                with el("div", "rows"):
                    for i, o in enumerate(d["relay"]):
                        out_row(("relay", i), f"R{i + 1}", f"GPIO{Io.PINS[i]}", o,
                                lambda field, v, o=o: remap(o, field, v))

            with el("section", "ctl set"):
                el("h2", "", "Lighting controllers", "CalTreat lighting controller boards on the wired network "
                                                     "(Modbus TCP), known by chip ID")
                found = {b.uid: b for b in finder.found}
                if not d["boards"]:
                    el("div", "mapdia", "Looking for boards on the wired network…")
                for uid, bc in sorted(d["boards"].items(), key=lambda x: x[1]["name"]):
                    rb = found.get(uid)
                    box = el("div", "board")
                    with box:
                        with el("div", "boardhd"):
                            with el("div", "hd-right"):
                                link = el("i", "lampdot", title="Connected")
                                name_in = el("input", "txt", type="text", value=bc["name"], maxlength="12",
                                             title="Board name")
                                name_in.on("change", lambda e, uid=uid: rename_board(uid, e.args),
                                           js_handler="(e) => emit(e.target.value)")
                            el("small", "", f"{'MAC ' + rb.mac + '<br>' if rb and rb.mac else ''}ID {uid}<br>"
                                            f"{rb.host if rb else 'not found'}", "MAC · chip ID · address")
                            sbtn("All ⏻", lambda uid=uid: io.all_off(uid), "All outputs by hand off, tests released")
                        with el("div", "rows"):
                            for i, o in enumerate(bc["outputs"]):
                                out_row((uid, i), f"GP{i}", f"{bc['name']}", o,
                                        lambda field, v, o=o: remap(o, field, v))

                    def show_board(link=link, box=box, uid=uid):
                        b = next((x for x in finder.found if x.uid == uid), None)
                        on = bool(b and b.connected)
                        link.classes(replace="lampdot on" if on else "lampdot")
                        box.classes(replace="board" if on else "board offline")
                    live.append(show_board)

            errs = ui.html("", sanitize=False, tag="div").classes("errs full")
            live.append(lambda: errs.set_content(_esc(errors())))

    def test_speaker(sp):
        s = cfg.data["speakers"][sp]
        on = router.test == (s["device"], s["ch"])
        router.set_test(None if on or s["device"] == "none" else s["device"], s["ch"])

    def raw_test(dev, ch):
        router.set_test(None if router.test == (dev, ch) else dev, ch)

    def add_fn(name):
        name = (name or "").strip()[:40]
        if not name:
            return
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "light"
        fid, n = base, 2
        while cfg.function(fid):
            fid, n = f"{base}-{n}", n + 1
        cfg.data["functions"].append({"id": fid, "name": name, "kind": "manual", "symbol": "●"})
        save()
        body.refresh()

    def delete_fn(fid):
        d = cfg.data
        for o in [*d["relay"], *[o for b in d["boards"].values() for o in b["outputs"]]]:
            if o["fn"] == fid:
                o["fn"] = None
        d["functions"] = [f for f in d["functions"] if f["id"] != fid]
        io.modes.pop(fid, None)
        save()
        body.refresh()

    def rename_board(uid, name):
        name = (name or "").strip()[:12]
        if not name or any(b["name"] == name for u, b in cfg.data["boards"].items() if u != uid):
            ui.notify("Name empty or used by another board", type="warning")
            body.refresh()
            return
        cfg.data["boards"][uid]["name"] = name
        save()

    body()
    shown_boards = [sorted(b.uid for b in finder.found), len(cfg.data["boards"])]

    def tick():
        now = [sorted(b.uid for b in finder.found), len(cfg.data["boards"])]
        if now != shown_boards:
            shown_boards[:] = now
            body.refresh()
            return
        for fn in list(live):
            fn()

    ui.timer(0.5, tick)


def main():
    ui.run(host="0.0.0.0", port=PORT, title="Caltreat", reload=False, show=False, dark=True, favicon="🚆")


if __name__ in {"__main__", "__mp_main__"}:
    main()
