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






"""Caltreat: a NiceGUI page that sets the Pi's 3.5 mm jack volume and plays Caltrain KISS EMU cab sounds.

The voices are numpy ports of the sound engine in publish.html: horn (one harmonic series near F#4 with its
measured harmonic levels, onset pitch overshoot, one-pole attack/release and faint air noise), the electronic
bell, the door-closing tone, the traction inverter whine, the air-release hiss and the brake squeal, plus the
horn signal patterns. Audio is synthesized in blocks and piped as raw PCM into `aplay` on the headphone card, so
no PortAudio or other system packages are needed; volume is the card's ALSA `PCM` control, set with `amixer`.
All browsers share the one speaker, so the engine state (bell, doors, throttle, ...) is shared too.

The page also switches the lights on a 4-channel relay shield (white head/ditch lights, red marker lights), and
GP0-GP7 on the CalTreat lighting controllers (Waveshare RP2350-ETH, Modbus TCP) found on the Pi's wired network.
"""

import fcntl
import json
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from nicegui import app, ui

SR = 48000
BLOCK = 512  # ~11 ms per synthesized block
ALSA_CARD = "Headphones"
ALSA_DEVICE = f"plughw:CARD={ALSA_CARD}"
MIXER_CONTROL = "PCM"
USB_MIXER_CONTROL = "Speaker"  # the C-Media USB cards' playback volume
MASTER = 1.1 * 0.85  # publish.html's default master volume, before its soft knee
# publish.html's default mix levels
HORN_INTENSITY = 0.85
BELL_LEVEL = 0.75
MOTOR_LEVEL = 0.8
DOOR_LEVEL = 0.7
HISS_LEVEL = 0.7
ACCEL_TIME = 14.0  # s, standstill to full ("Costume")
LOOP = 1 << 18  # ~5.5 s noise loops

# Lighting controller boards get DHCP addresses on eth0 (NetworkManager's shared connection: the Pi is
# 192.168.50.1 and its dnsmasq leases .10-.254). They are found by scanning that range, and named A, B, ... by chip
# ID in the order they are first seen; the names are kept in REMOTE_NAMES_FILE.
REMOTE_NET = "192.168.50."
REMOTE_HOSTS = range(10, 255)
REMOTE_NAMES_FILE = Path.home() / ".config" / "caltreat" / "boards.json"
REMOTE_PINS = 8  # GP0-GP7 are switched from the page (GP0-GP3 drive the MOSFET module's lights 1-4)

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

# (name, signal, [(start s, duration s), ...]); – long, o short
PATTERNS = [
    ("Crossing", "– – o –", [(0, 2.0), (2.6, 2.0), (5.2, 0.7), (6.4, 3.5)]),
    ("Proceed", "– –", [(0, 1.6), (2.1, 1.6)]),
    ("Acknowledge", "o o", [(0, 0.5), (0.9, 0.5)]),
    ("Approaching", "–", [(0, 2.5)]),
    ("Backing up", "o o o", [(0, 0.5), (0.9, 0.5), (1.8, 0.5)]),
    ("Horn show", "o o – o –", [(0, 0.35), (0.6, 0.35), (1.2, 1.4), (3.0, 0.35), (3.6, 2.2)]),
]


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
            self.speed = max(self.throttle, self.speed - dt / self.coast_time)
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
    """Mixes all voices and streams them to `aplay` from a background thread, restarting it if the device goes
    away. Controls may be called from any thread."""

    DOOR_TAP = 0.35  # s; a shorter press sounds the door tone for DOOR_TAP_LEN
    DOOR_TAP_LEN = 3.0

    def __init__(self):
        rng = np.random.default_rng(54321)
        self.horn = Horn()
        self.bell = Bell(rng)
        self.door = Door()
        self.motor = Motor(rng)
        self.hiss = Hiss(rng)
        self.squeal = Squeal(rng)
        self.voices = [self.horn, self.bell, self.door, self.motor, self.hiss, self.squeal]
        self.lock = threading.Lock()
        self.t = 0  # absolute sample clock
        self.holders = set()  # clients holding the horn
        self.door_holders = {}  # client -> press time
        self.door_until = 0.0
        self.pattern = []  # [(start, end) absolute samples]
        self.pattern_idx = None
        self.running = False
        self.error = None
        self.thread = None

    # ---------- controls ----------
    def hold(self, who, on):
        with self.lock:
            if on:
                self.pattern = []  # holding the horn cancels a running signal
                self.holders.add(who)
            else:
                self.holders.discard(who)

    def toggle_bell(self):
        with self.lock:
            self.bell.set_on(not self.bell.on)

    def door_down(self, who):
        with self.lock:
            self.door_until = 0.0
            self.door_holders[who] = time.monotonic()

    def door_up(self, who):
        with self.lock:
            t0 = self.door_holders.pop(who, None)
            if t0 is not None and time.monotonic() - t0 < self.DOOR_TAP:
                self.door_until = t0 + self.DOOR_TAP_LEN

    def air(self):
        self.hiss.trigger()

    def brakes(self):
        self.squeal.trigger()

    def set_throttle(self, v):
        self.motor.throttle = min(max(float(v), 0.0), 1.0)

    def play_pattern(self, i):
        with self.lock:
            self.pattern = [(self.t + round(s * SR), self.t + round((s + d) * SR)) for s, d in PATTERNS[i][2]]
            self.pattern_idx = i

    def release(self, who):
        """Never leave the horn or door tone stuck on when a client goes away."""
        with self.lock:
            self.holders.discard(who)
            self.door_holders.pop(who, None)

    # ---------- state for the UI ----------
    @property
    def door_on(self):
        return bool(self.door_holders) or time.monotonic() < self.door_until

    @property
    def horn_on(self):
        return self.horn.gate

    @property
    def busy_pattern(self):
        return self.pattern_idx if self.pattern else None

    # ---------- audio thread ----------
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, name="caltreat-audio", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)

    def render(self):
        with self.lock:
            t0 = self.t
            while self.pattern and t0 >= self.pattern[0][1]:
                self.pattern.pop(0)
            pat = bool(self.pattern) and t0 >= self.pattern[0][0]
            self.horn.set_gate(bool(self.holders) or pat)
            self.door.gate = self.door_on
            out = np.zeros(BLOCK)
            for v in self.voices:
                y = v.render(t0)
                if y is not None:
                    out += y
            self.t += BLOCK
        return soft_knee(out * MASTER)

    def _run(self):
        while self.running:
            proc = subprocess.Popen(
                ["aplay", "-q", "-D", ALSA_DEVICE, "-t", "raw", "-f", "S16_LE", "-r", str(SR), "-c", "1",
                 "--buffer-time=60000", "--period-time=15000"],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                fcntl.fcntl(proc.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)  # keep the pipe from adding latency
            except OSError:
                pass
            try:
                while self.running:
                    proc.stdin.write((self.render() * 32767).astype("<i2").tobytes())
                    self.error = None
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                proc.terminate()
                err = proc.stderr.read().decode(errors="replace").strip()
                proc.wait()
            if self.running:
                self.error = err or "aplay exited"
                print(f"caltreat: audio output failed, retrying: {self.error}", file=sys.stderr)
                time.sleep(2)


def get_volume(card=ALSA_CARD, control=MIXER_CONTROL):
    out = subprocess.run(["amixer", "-c", card, "-M", "sget", control],
                         capture_output=True, text=True).stdout
    m = re.search(r"\[(\d+)%\]", out)
    return int(m.group(1)) if m else 50


def set_volume(pct, card=ALSA_CARD, control=MIXER_CONTROL):
    subprocess.run(["amixer", "-c", card, "-M", "-q", "sset", control, f"{int(pct)}%"])


class Lights:
    """The Inland (Keyestudio KS0212-style) 4-channel relay shield: white head/ditch lights on channels 1-2, red
    marker lights on 3-4. Each pair is OFF, ON or FLASH (alternating left/right, as ditch lights and crossing
    flashers do); with `horn_flash` the white pair also alternates while the horn sounds and for a few seconds
    after, as US ditch lights do at crossings. Relays are driven with `pinctrl`, so no GPIO libraries are needed
    (the user must be in the `gpio` group)."""

    PINS = (4, 22, 6, 26)  # BCM GPIO for relay channels 1-4 (J2-J5 on the shield)
    ACTIVE_HIGH = True  # the shield energizes a relay when its GPIO is high
    FLASH_HALF = 0.5  # s per side: 60 flashes/min each
    HORN_HOLD = 5.0  # s the ditch lights keep flashing after the horn stops
    MODES = ("off", "on", "flash")

    def __init__(self, audio):
        self.audio = audio
        self.white = "off"
        self.red = "off"
        self.horn_flash = True
        self.state = [False] * 4  # relay channels as last written
        self.last_horn = -1e9
        self.error = None
        self.running = False
        self.thread = None

    def cycle(self, pair):
        setattr(self, pair, self.MODES[(self.MODES.index(getattr(self, pair)) + 1) % len(self.MODES)])

    def desired(self, now):
        if self.audio.horn_on:
            self.last_horn = now
        phase = int(now / self.FLASH_HALF) % 2 == 0
        white = self.white
        if white == "on" and self.horn_flash and now - self.last_horn < self.HORN_HOLD:
            white = "flash"

        def pair(mode):
            return {"off": [False, False], "on": [True, True], "flash": [phase, not phase]}[mode]

        return pair(white) + pair(self.red)

    def _pinctrl(self, pins, *opts):
        r = subprocess.run(["pinctrl", "set", ",".join(map(str, pins)), *opts], capture_output=True, text=True)
        if r.returncode:
            raise OSError(r.stderr.strip() or f"pinctrl exited with {r.returncode}")

    def _write(self, want, force=False):
        changed = [(p, w) for p, w, s in zip(self.PINS, want, self.state) if force or w != s]
        for level in (True, False):
            pins = [p for p, w in changed if w == level]
            if pins:
                self._pinctrl(pins, "op", "dh" if level == self.ACTIVE_HIGH else "dl")
        self.state = list(want)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, name="caltreat-lights", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        try:
            self._write([False] * 4, force=True)
        except OSError:
            pass

    def _run(self):
        force = True
        while self.running:
            try:
                self._write(self.desired(time.monotonic()), force)
                force, self.error = False, None
            except OSError as e:
                if self.error is None:
                    print(f"caltreat: relay output failed: {e}", file=sys.stderr)
                self.error, force = str(e), True
                time.sleep(2)
            time.sleep(0.05)


class ModbusError(OSError):
    pass


class ModbusTCP:
    """Just enough of a Modbus TCP client for the lighting controller boards (function codes 4, 15, 16 and 43), on
    a plain socket so no Modbus package is needed. Any error means the connection should be dropped."""

    def __init__(self, host, port=502, timeout=1.0):
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
        self.connected = False
        self.error = None
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.running = False
        self.thread = None

    def toggle(self, i):
        with self.lock:
            self.want[i] = not self.want[i]
        self.wake.set()

    def set_all(self, on):
        with self.lock:
            self.want = [bool(on)] * REMOTE_PINS
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

    def __init__(self):
        self.boards = {}  # chip ID -> RemoteBoard
        self.names = self._load_names()  # chip ID -> name, kept across restarts
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.running = False
        self.thread = None

    @property
    def found(self):
        """The boards found so far, by name."""
        with self.lock:
            return sorted(self.boards.values(), key=lambda b: b.name)

    @staticmethod
    def _load_names():
        try:
            return dict(json.loads(REMOTE_NAMES_FILE.read_text()))
        except (OSError, ValueError, TypeError):
            return {}

    def _name(self, uid):
        if uid not in self.names:
            used = set(self.names.values())
            self.names[uid] = next(c for c in map(chr, range(ord("A"), ord("Z") + 1)) if c not in used)
            try:
                REMOTE_NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
                REMOTE_NAMES_FILE.write_text(json.dumps(self.names, indent=2) + "\n")
            except OSError as e:
                print(f"caltreat: could not save board names: {e}", file=sys.stderr)
        return self.names[uid]

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
            with self.lock:
                board = self.boards.get(uid)
                if board is None:
                    board = self.boards[uid] = RemoteBoard(self._name(uid), uid, host)
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


def usb_outputs():
    """[(ALSA card id, USB port path)] for the USB sound cards, ordered by port. Card ids (Device, Device_1, ...)
    follow enumeration order and can swap between boots; the port path is fixed by where a card is plugged in."""
    try:
        cards = open("/proc/asound/cards").read()
    except OSError:
        return []
    found = re.findall(r"^\s*\d+ \[(\S+?)\s*\]: USB-Audio - .*\n\s*.* at usb-[^-]+-([\d.]+)", cards, re.M)
    return sorted(found, key=lambda c: [int(n) for n in c[1].split(".")])


class SpeakerTest:
    """A test tone on one channel of one USB sound card, for checking speakers and wiring. Runs its own `aplay`
    (stereo, tone on the chosen side, silence on the other), independent of the main sound output."""

    FREQ = 440.0
    LEVEL = db(-6)
    CHANNELS = ("left", "right")

    def __init__(self):
        self.target = None  # (card id, channel index)
        self.on = False
        self.error = None
        self.thread = None
        self.lock = threading.Lock()

    def set_target(self, card, ch):
        self.target = (card, ch)

    def set_on(self, on):
        with self.lock:
            self.on = bool(on) and self.target is not None
            if self.on and not (self.thread and self.thread.is_alive()):
                self.thread = threading.Thread(target=self._run, name="caltreat-speaker-test", daemon=True)
                self.thread.start()

    def _run(self):
        n = np.arange(BLOCK)
        fade = np.minimum(np.arange(1, BLOCK + 1) / BLOCK, 1.0)  # one block of fade in/out to avoid clicks
        pos = 0
        while self.on:
            card, ch = self.target
            proc = subprocess.Popen(
                ["aplay", "-q", "-D", f"plughw:CARD={card}", "-t", "raw", "-f", "S16_LE", "-r", str(SR), "-c", "2",
                 "--buffer-time=60000", "--period-time=15000"],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            first = True
            try:
                while True:
                    ending = not self.on or self.target != (card, ch)
                    tone = np.sin(2 * np.pi * self.FREQ * (pos + n) / SR) * self.LEVEL
                    pos = (pos + BLOCK) % SR
                    if first:
                        tone *= fade
                    elif ending:
                        tone *= fade[::-1]
                    first = False
                    frames = np.zeros((BLOCK, 2))
                    frames[:, ch] = tone
                    proc.stdin.write((frames * 32767).astype("<i2").tobytes())
                    if ending:
                        break
                proc.stdin.close()
                proc.wait(timeout=2)
                self.error = None
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                proc.kill()
                self.error = proc.stderr.read().decode(errors="replace").strip() or "aplay exited"
                print(f"caltreat: speaker test failed: {self.error}", file=sys.stderr)
                self.on = False
                break


audio = AudioOut()
lights = Lights(audio)
speaker_test = SpeakerTest()
finder = BoardFinder()
app.on_startup(audio.start)
app.on_startup(lights.start)
app.on_startup(finder.start)
app.on_shutdown(audio.stop)
app.on_shutdown(lights.stop)
app.on_shutdown(finder.stop)
app.on_shutdown(lambda: speaker_test.set_on(False))


def hold_button(btn, down, up):
    btn.on("pointerdown", down)
    for ev in ("pointerup", "pointercancel", "pointerleave"):
        btn.on(ev, up)
    btn.on("contextmenu", js_handler="(e) => e.preventDefault()")


@ui.page("/")
def index(client):
    ui.dark_mode(True)
    ui.add_css("""
        .hold, .pad, .pat, .io { touch-action: none; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none; }
        .horn-btn.held { filter: brightness(1.35); transform: translateY(3px); }
        .pad { min-height: 76px; border: 2px solid #2b3036; transition: transform .05s, background .1s; }
        .pad:active { transform: scale(.98); }
        .pad.bell.on { background: #3a2a0c !important; border-color: #f5a623; box-shadow: 0 0 18px rgba(245,166,35,.35); }
        .pad.door.on { background: #0e3321 !important; border-color: #2fd07a; box-shadow: 0 0 18px rgba(47,208,122,.3); }
        .pad.tone.on { background: #0e3321 !important; border-color: #2fd07a; box-shadow: 0 0 18px rgba(47,208,122,.3); }
        .pat { border: 1px solid #3b424b; }
        .pat.busy { border-color: #e1251b; }
        .lamp i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #26302a; margin-right: 6px; }
        .lamp.on.horn i { background: #e1251b; box-shadow: 0 0 10px #e1251b; }
        .lamp.on.bell i { background: #f5a623; box-shadow: 0 0 10px #f5a623; }
        .lamp.on.door i { background: #2fd07a; box-shadow: 0 0 10px #2fd07a; }
        .lcd { background: #0a0f0c; color: #9ff0bd; font-family: ui-monospace, monospace; }
        .lamp.on.white i { background: #f4f1e6; box-shadow: 0 0 12px #fffbe8; }
        .lamp.on.red i { background: #e1251b; box-shadow: 0 0 10px #e1251b; }
        .lamp.on.link i { background: #2fd07a; box-shadow: 0 0 10px #2fd07a; }
        .sw { display: flex; border: 2px solid #2b3036; border-radius: 10px; overflow: hidden; background: #14171b; }
        .sw .q-btn { flex: 1; min-height: 48px; border-radius: 0; font-weight: 700; letter-spacing: .1em; }
        .io { min-height: 48px; border: 2px solid #2b3036; font-weight: 700; letter-spacing: .05em; transition: background .1s; }
        .io.on { background: #0e3321 !important; border-color: #2fd07a; box-shadow: 0 0 14px rgba(47,208,122,.3); }
        .board.offline .io { opacity: .45; }
    """)
    who = client.id

    def press():
        audio.hold(who, True)
        horn_btn.classes(add="held")

    def release():
        audio.hold(who, False)
        horn_btn.classes(remove="held")

    client.on_disconnect(lambda: audio.release(who))

    with ui.column().classes("w-full max-w-md mx-auto p-4 gap-4"):
        ui.label("Caltreat").classes("text-3xl font-bold tracking-wider")

        with ui.card().classes("w-full"):
            vol_label = ui.label()
            vol = ui.slider(min=0, max=100, step=1, value=get_volume()).props("label")

            def on_volume(e):
                set_volume(e.value)
                vol_label.text = f"Headphone jack volume: {int(e.value)} %"

            vol.on_value_change(on_volume)
            vol_label.text = f"Headphone jack volume: {int(vol.value)} %"

        # cab display
        with ui.row().classes("lcd w-full rounded-lg p-3 items-end justify-between no-wrap"):
            with ui.column().classes("gap-0"):
                ui.label("SPEED").classes("text-xs opacity-60")
                mph = ui.label().classes("text-3xl")
            with ui.column().classes("gap-0"):
                ui.label("INVERTER").classes("text-xs opacity-60")
                hz = ui.label().classes("text-3xl")
            with ui.column().classes("gap-1 text-xs"):
                lamps = {k: ui.html(f"<i></i>{k.upper()}").classes(f"lamp {k}") for k in ("horn", "bell", "door")}

        # power controller
        with ui.card().classes("w-full gap-1"):
            thr_label = ui.label()
            thr = ui.slider(min=0, max=100, step=1, value=round(audio.motor.throttle * 100))
            with ui.row().classes("w-full justify-between text-xs opacity-60 px-2"):
                for n in ("OFF", "25", "50", "75", "FULL"):
                    ui.label(n)

            def on_throttle(e):
                audio.set_throttle(e.value / 100)
                thr_label.text = f"Power controller: {int(e.value)} %"

            thr.on_value_change(on_throttle)
            thr_label.text = f"Power controller: {int(thr.value)} %"

        # pads
        def pad(title, sub, cls):
            with ui.button().classes(f"pad {cls} w-full rounded-xl").props("unelevated color=grey-10 no-caps align=left") as b:
                with ui.column().classes("gap-1 items-start text-left"):
                    ui.label(title).classes("text-xl font-bold uppercase tracking-wide")
                    ui.label(sub).classes("text-xs opacity-60")
            return b

        with ui.grid(columns=2).classes("w-full gap-3"):
            bell_btn = pad("Bell", "Tap on / off · 2.5 strikes/s", "bell")
            door_btn = pad("Doors", "Tap: 3 s · hold: as long as held", "door hold")
            air_btn = pad("Air", "Brake release hiss", "")
            brake_btn = pad("Brakes", "Squeal while stopping", "")
        bell_btn.on("pointerdown", audio.toggle_bell)
        hold_button(door_btn, lambda: audio.door_down(who), lambda: audio.door_up(who))
        air_btn.on("pointerdown", audio.air)
        brake_btn.on("pointerdown", audio.brakes)

        horn_btn = ui.button("HORN").classes("horn-btn hold w-full h-40 text-5xl font-bold tracking-widest") \
            .props("color=red unelevated")
        hold_button(horn_btn, press, release)

        # horn signals
        with ui.card().classes("w-full gap-2"):
            ui.label("Horn signals  (– long · o short)").classes("text-sm opacity-70")
            with ui.grid(columns=2).classes("w-full gap-2"):
                pat_btns = []
                for i, (name, sig, _) in enumerate(PATTERNS):
                    with ui.button().classes("pat w-full rounded-lg").props("unelevated color=grey-9 no-caps") as b:
                        with ui.row().classes("w-full justify-between items-center no-wrap"):
                            ui.label(name).classes("font-bold uppercase text-left")
                            ui.label(sig).classes("font-mono text-red-500 tracking-widest whitespace-nowrap")
                    b.on("pointerdown", lambda i=i: audio.play_pattern(i))
                    pat_btns.append(b)

        # lights (relay shield)
        with ui.card().classes("w-full gap-3"):
            ui.label("Lights").classes("text-sm opacity-70 uppercase tracking-widest")
            with ui.row().classes("lcd w-full rounded-lg px-3 py-2 justify-between no-wrap text-xs"):
                light_lamps = [ui.html(f"<i></i>{name}").classes(f"lamp {color}") for name, color in
                               (("HEAD L", "white"), ("HEAD R", "white"), ("MARK L", "red"), ("MARK R", "red"))]
            switches = {}
            for pair, title, sub, color, text in (("white", "Headlights", "Ch 1–2 · white · flash = ditch lights",
                                                   "grey-2", "black"),
                                                  ("red", "Marker lights", "Ch 3–4 · red · flash = alternate",
                                                   "red", "white")):
                with ui.column().classes("w-full gap-1"):
                    with ui.row().classes("w-full items-baseline justify-between"):
                        ui.label(title).classes("text-xl font-bold uppercase tracking-wide")
                        ui.label(sub).classes("text-xs opacity-60")
                    sw = ui.toggle({m: m.upper() for m in Lights.MODES}, value=getattr(lights, pair)) \
                        .classes("sw w-full") \
                        .props(f"unelevated spread toggle-color={color} toggle-text-color={text} color=grey-10")
                    sw.on_value_change(lambda e, pair=pair: setattr(lights, pair, e.value or "off"))
                    switches[pair] = sw
            horn_flash = ui.switch("Ditch lights flash with horn", value=lights.horn_flash) \
                .props("color=red").classes("text-sm")
            horn_flash.on_value_change(lambda e: setattr(lights, "horn_flash", e.value))

        # lighting controllers (Modbus TCP on eth0)
        shown = {}  # state last sent to this browser, see show()
        board_cols, board_links, io_btns, shown_boards = [], [], [], []

        @ui.refreshable
        def board_section():
            for items in (board_cols, board_links, io_btns):
                items.clear()
            shown_boards[:] = finder.found
            for key in [k for k in shown if k.startswith(("link", "off", "io"))]:
                del shown[key]
            ui.label("Lighting controllers").classes("text-sm opacity-70 uppercase tracking-widest")
            if not shown_boards:
                ui.label("Looking for boards on the wired network…").classes("text-sm opacity-60")
            for board in shown_boards:
                with ui.column().classes("board offline w-full gap-2") as col:
                    with ui.row().classes("w-full items-center justify-between no-wrap"):
                        ui.label(f"Board {board.name}").classes("text-xl font-bold uppercase tracking-wide")
                        with ui.row().classes("items-center gap-2 no-wrap"):
                            board_links.append(ui.html(f"<i></i>{board.host}").classes("lamp link lcd text-xs "
                                                                                       "rounded px-2 py-1"))
                            ui.button("All off", on_click=lambda board=board: board.set_all(False)) \
                                .props("flat dense no-caps size=sm color=grey-5")
                    btns = []
                    with ui.grid(columns=4).classes("w-full gap-2"):
                        for i in range(REMOTE_PINS):
                            btn = ui.button(f"GP{i}").classes("io w-full rounded-lg").props("unelevated color=grey-10")
                            btn.on("pointerdown", lambda board=board, i=i: board.toggle(i))
                            btns.append(btn)
                board_cols.append(col)
                io_btns.append(btns)

        with ui.card().classes("w-full gap-4"):
            board_section()

        # speaker test (USB sound cards)
        with ui.card().classes("w-full gap-3"):
            ui.label("Speaker test").classes("text-sm opacity-70 uppercase tracking-widest")
            usb_cards = [card for card, _ in usb_outputs()]
            if usb_cards:
                usb_vol_label = ui.label()
                usb_vol = ui.slider(min=0, max=100, step=1, value=get_volume(usb_cards[0], USB_MIXER_CONTROL)) \
                    .props("label")

                def on_usb_volume(e):
                    for card in usb_cards:
                        set_volume(e.value, card, USB_MIXER_CONTROL)
                    usb_vol_label.text = f"USB sound card volume: {int(e.value)} %"

                usb_vol.on_value_change(on_usb_volume)
                usb_vol_label.text = f"USB sound card volume: {int(usb_vol.value)} %"
            outputs = {f"{card}:{i}": f"USB {port} ({card}) · {side.title()}"
                       for card, port in usb_outputs() for i, side in enumerate(SpeakerTest.CHANNELS)}
            if speaker_test.target and f"{speaker_test.target[0]}:{speaker_test.target[1]}" not in outputs:
                speaker_test.set_on(False)
            current = f"{speaker_test.target[0]}:{speaker_test.target[1]}" if speaker_test.target else None
            current = current if current in outputs else next(iter(outputs), None)

            def pick(key):
                if key:
                    card, ch = key.rsplit(":", 1)
                    speaker_test.set_target(card, int(ch))

            ch_select = ui.select(outputs, value=current, label="Channel").classes("w-full") \
                .props("outlined dark options-dense")
            ch_select.on_value_change(lambda e: pick(e.value))
            pick(current)
            tone_btn = pad("Test tone", f"{SpeakerTest.FREQ:.0f} Hz · tap on / off", "tone")
            tone_btn.on("pointerdown", lambda: speaker_test.set_on(not speaker_test.on))
            if not outputs:
                ui.label("No USB sound cards found. Plug one in and reload.").classes("text-sm text-red-400")
                tone_btn.disable()

        ui.label("Keys: Space/H horn (hold) · B bell · D doors (hold) · A air · S brakes · ↑↓ power · "
                 "0 power off · 1–6 horn signals · L headlights · M markers").classes("text-sm opacity-70")

        status = ui.label().classes("text-sm text-red-400")

        def show_errors():
            errs = [f"Audio output problem: {audio.error}" if audio.error else "",
                    f"Relay problem: {lights.error}" if lights.error else "",
                    f"Speaker test problem: {speaker_test.error}" if speaker_test.error else "",
                    *(f"Board {b.name} ({b.host}): {b.error}" for b in finder.found if b.error and not b.connected)]
            status.set_text(" · ".join(e for e in errs if e))

        ui.timer(1.0, show_errors)

    # Reflect the shared engine state (other browsers may be pressing things too); only send changes.
    def show(key, el, cls, on):
        if shown.get(key) != on:
            shown[key] = on
            el.classes(add=cls) if on else el.classes(remove=cls)

    def tick():
        mph.text = f"{round(audio.motor.speed * 79)} mph"
        hz.text = f"{round(audio.motor.speed * MOTOR['a_max'])} Hz"
        show("lh", lamps["horn"], "on", audio.horn_on)
        show("lb", lamps["bell"], "on", audio.bell.on)
        show("ld", lamps["door"], "on", audio.door_on)
        show("bell", bell_btn, "on", audio.bell.on)
        show("door", door_btn, "on", audio.door_on)
        busy = audio.busy_pattern
        for i, b in enumerate(pat_btns):
            show(f"p{i}", b, "busy", busy == i)
        if round(thr.value) != round(audio.motor.throttle * 100):
            thr.value = round(audio.motor.throttle * 100)
        for i, lamp in enumerate(light_lamps):
            show(f"r{i}", lamp, "on", lights.state[i])
        for pair, sw in switches.items():
            if sw.value != getattr(lights, pair):
                sw.value = getattr(lights, pair)
        if horn_flash.value != lights.horn_flash:
            horn_flash.value = lights.horn_flash
        if finder.found != shown_boards:
            board_section.refresh()
        for bi, board in enumerate(shown_boards):
            show(f"link{bi}", board_links[bi], "on", board.connected)
            if board_links[bi].content != f"<i></i>{board.host}":
                board_links[bi].content = f"<i></i>{board.host}"
            show(f"off{bi}", board_cols[bi], "offline", not board.connected)
            for i, btn in enumerate(io_btns[bi]):
                show(f"io{bi}.{i}", btn, "on", board.want[i])
        show("tone", tone_btn, "on", speaker_test.on)
        if speaker_test.target:
            key = f"{speaker_test.target[0]}:{speaker_test.target[1]}"
            if key != ch_select.value and key in outputs:
                ch_select.value = key

    ui.timer(0.1, tick)

    held = set()

    def on_key(e):
        if e.action.repeat or e.modifiers.ctrl or e.modifiers.meta or e.modifiers.alt:
            return
        k = e.key.name.lower()
        if k in (" ", "h"):
            if e.action.keydown:
                held.add(k)
                press()
            else:
                held.discard(k)
                if not held:
                    release()
        elif k == "d":
            audio.door_down(who) if e.action.keydown else audio.door_up(who)
        elif not e.action.keydown:
            return
        elif k == "b":
            audio.toggle_bell()
        elif k == "a":
            audio.air()
        elif k == "s":
            audio.brakes()
        elif e.key.arrow_up:
            thr.value = min(100, thr.value + 10)
        elif e.key.arrow_down:
            thr.value = max(0, thr.value - 10)
        elif k == "0":
            thr.value = 0
        elif len(k) == 1 and k in "123456":
            audio.play_pattern(int(k) - 1)
        elif k == "l":
            lights.cycle("white")
        elif k == "m":
            lights.cycle("red")

    ui.keyboard(on_key=on_key, ignore=["input", "select", "textarea"])


def main():
    ui.run(host="0.0.0.0", port=8080, title="Caltreat", reload=False, show=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()