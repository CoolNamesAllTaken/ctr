"""Push-to-talk intercom: a phone's microphone, streamed over a WebSocket, played on the INSIDE speakers.

The page captures 16 kHz mono with an AudioWorklet (echo cancellation, noise suppression and auto gain on) and sends
20 ms frames: a 12-byte header (uint32 sequence number, float64 capture time in ms since the epoch, little endian)
and 320 int16 samples. One talker at a time: `claim()` fails while someone else talks. The output side calls
`pull(n)` for n samples at 48 kHz from the audio thread: a small jitter buffer (40 ms before it starts, kept under
MAX_MS by dropping the oldest audio) upsampled x3. Talking stops on release, on a disconnect, when no frames come
for STALE s, or after the timeout.
"""

import struct
import threading
import time

import numpy as np

IN_RATE = 16000
OUT_RATE = 48000
UP = OUT_RATE // IN_RATE
FRAME = 320  # 20 ms
HEADER = struct.Struct("<Id")
PREBUF_MS = 40
MAX_MS = 200
TARGET_MS = 80  # what an overfull buffer is cut back to
STALE = 1.5  # s without frames = the talker is gone


def _lowpass(taps=97, cutoff=7500.0):
    """Windowed-sinc interpolation filter for x3 upsampling (gain UP, so the zero-stuffed signal keeps its level)."""
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff / OUT_RATE * n) * np.hamming(taps)
    return h / h.sum() * UP


class Upsampler:
    """x3 zero-stuff + FIR, continuous across calls."""

    H = _lowpass()

    def __init__(self):
        self.hist = np.zeros(len(self.H) - 1)

    def __call__(self, x):
        z = np.zeros(len(x) * UP)
        z[::UP] = x
        y = np.convolve(np.concatenate([self.hist, z]), self.H, mode="valid")
        self.hist = np.concatenate([self.hist, z])[-(len(self.H) - 1):]
        return y


class Intercom:
    def __init__(self, settings=None):
        self.settings = settings or (lambda: {"mic_gain_db": 0, "level": 80, "duck_db": -18, "timeout_s": 60})
        self.lock = threading.Lock()
        self.talker = None  # token of whoever is talking
        self.who = None  # their page's client id, for the "busy" display
        self.started = 0.0
        self.last_frame = 0.0
        self.buf = np.zeros(0, dtype=np.float32)  # 16 kHz samples waiting
        self.out = np.zeros(0)  # upsampled, not yet pulled
        self.playing = False  # False while (re)filling the jitter buffer
        self.up = Upsampler()
        self.level = 0.0  # input RMS of the last frame, 0..1
        self.stats = {}
        self.marks = []  # (output sample index, capture time ms, n): where frames start in the pulled stream (tests)
        self.pending = []  # (input sample index, capture time ms, n) of frames not yet upsampled
        self.fed = 0  # input index of the next sample fed
        self.consumed = 0  # input index of buf[0]
        self.pulled = 0  # samples pulled since the talk started
        self.keep_marks = False
        self._reset_stats()

    def _reset_stats(self):
        self.stats = {"frames": 0, "lost": 0, "underruns": 0, "dropped_ms": 0.0, "buffer_ms": 0.0}
        self.next_seq = None

    # ---------- talker ----------
    def claim(self, token, who=None):
        with self.lock:
            if self.talker is not None and self.talker != token:
                return False
            self.talker, self.who = token, who
            self.started = self.last_frame = time.monotonic()
            self.buf = np.zeros(0, dtype=np.float32)
            self.out = np.zeros(0)
            self.playing = False
            self.up = Upsampler()
            self.pulled = self.fed = self.consumed = 0
            self.marks, self.pending = [], []
            self._reset_stats()
            return True

    def release(self, token):
        with self.lock:
            if self.talker == token:
                self.talker = self.who = None
                self.level = 0.0

    @property
    def talking(self):
        return self.talker is not None

    @property
    def active(self):
        """Talking or still playing out what was said (the speakers stay ducked)."""
        return self.talker is not None or len(self.buf) > 0 or len(self.out) > 0

    def expired(self):
        """Why the current talker should be stopped (timeout / stale), or None."""
        if self.talker is None:
            return None
        now = time.monotonic()
        if now - self.started > self.settings()["timeout_s"]:
            return "timeout"
        if now - self.last_frame > STALE:
            return "stale"
        return None

    def feed(self, token, data):
        """One frame from the talker (bytes: header + int16 PCM). Returns False if token isn't the talker."""
        if len(data) <= HEADER.size or (len(data) - HEADER.size) % 2:
            return True
        seq, t_ms = HEADER.unpack_from(data)
        pcm = np.frombuffer(data, dtype="<i2", offset=HEADER.size).astype(np.float32) / 32768.0
        with self.lock:
            if token != self.talker:
                return False
            self.last_frame = time.monotonic()
            if self.next_seq is not None and seq > self.next_seq:
                self.stats["lost"] += seq - self.next_seq
            self.next_seq = seq + 1
            self.stats["frames"] += 1
            self.level = float(np.sqrt(np.mean(pcm ** 2))) if len(pcm) else 0.0
            if self.keep_marks:
                self.pending.append((self.fed, t_ms, len(pcm)))
            self.fed += len(pcm)
            self.buf = np.concatenate([self.buf, pcm])
            if len(self.buf) > MAX_MS * IN_RATE // 1000:
                cut = len(self.buf) - TARGET_MS * IN_RATE // 1000
                self.buf = self.buf[cut:]
                self.consumed += cut
                self.stats["dropped_ms"] += cut * 1000 / IN_RATE
        return True

    # ---------- output (audio thread) ----------
    def pull(self, n):
        """n samples at 48 kHz (float, gain applied), or None when there is nothing to play."""
        with self.lock:
            if not self.active:
                return None
            s = self.settings()
            if not self.playing:
                if len(self.buf) >= PREBUF_MS * IN_RATE // 1000 or (self.talker is None and len(self.buf)):
                    self.playing = True
                elif len(self.out) == 0:
                    self.pulled += n
                    return np.zeros(n)
            need = n - len(self.out)
            if need > 0 and self.playing:
                k = min(len(self.buf), -(-need // UP))
                if k:
                    base = self.pulled + len(self.out)
                    if self.pending:
                        c = self.consumed
                        self.marks += [(base + (i - c) * UP, t, m) for i, t, m in self.pending if c <= i < c + k]
                        self.pending = [p for p in self.pending if p[0] >= c + k]
                    self.out = np.concatenate([self.out, self.up(self.buf[:k])])
                    self.buf = self.buf[k:]
                    self.consumed += k
            y = self.out[:n]
            self.out = self.out[n:]
            if len(y) < n:
                if self.talker is not None and self.playing:
                    self.stats["underruns"] += 1
                self.playing = False  # refill before playing again
                y = np.concatenate([y, np.zeros(n - len(y))])
            self.pulled += n
            self.stats["buffer_ms"] = (len(self.buf) * UP + len(self.out)) * 1000 / OUT_RATE
            gain = 10 ** (s["mic_gain_db"] / 20) * s["level"] / 100
        return np.clip(y * gain, -1.0, 1.0)

    def duck(self):
        """Gain for the train sounds on the intercom's speakers (1 when nobody talks)."""
        return 10 ** (self.settings()["duck_db"] / 20) if self.active else 1.0
