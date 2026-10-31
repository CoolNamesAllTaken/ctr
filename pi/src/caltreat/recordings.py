"""Recordings uploaded from the settings page, played on any of the speakers (FRONT / INSIDE / REAR) next to the train
sounds, which they can duck while they play.

Files: <config folder>/recordings/<id>.wav, 16-bit mono at the app's rate (the browser decodes and resamples
uploads, so the Pi needs no ffmpeg; a plain WAV of another rate or channel count is converted here). Metadata
(name, level, loop, speakers, duck, pinned) is in config.json under "recordings", so the settings download carries
it; `zip_bytes()` / `restore_zip()` carry the audio too. Never in the repo.

    store = Store(cfg)
    meta = store.add("Boo", wav_bytes)        # ValueError: not a usable WAV, too long, too big
    player = Player(store); player.play(meta["id"])
    mix = player.pull(512)                    # Router, per block: {speaker: (recordings, train gain)}
"""

import io
import json
import os
import re
import secrets
import struct
import threading
import time
import zipfile
from pathlib import Path

import numpy as np

SR = 48000
SPEAKERS = ("front", "inside", "rear")
MAX_SECONDS = 600  # 10 min
MAX_BYTES = 100 * 1024 * 1024  # an upload (WAV as sent) or a zip member
MAX_ZIP = 2 * 1024 ** 3
ID_RE = re.compile(r"^[a-z0-9]{8,32}$")
FADE = int(0.01 * SR)  # 10 ms fade in / out, so start and stop don't click
DUCK_RAMP = 0.03 * SR  # train ducking ramps over ~30 ms, as for the intercom


def defaults(name, duration):
    return {"name": name, "duration": round(duration, 2), "level": 100, "loop": False, "speakers": ["inside"],
            "duck_db": 0, "pinned": False, "added": time.time()}


def clean_name(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    name = re.sub(r"\.(wav|mp3|m4a|aac|ogg|oga|opus|flac|webm|mp4)$", "", name, flags=re.I)
    return name[:40] or "Recording"


def read_wav(data):
    """WAV bytes -> (float mono samples at SR). ValueError if it isn't 8/16/24/32-bit PCM or float WAV."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt, fmt_body, raw = 12, None, b"", None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            fmt, fmt_body = struct.unpack_from("<HHIIHH", body), body
        elif cid == b"data":
            raw = body
            break
        pos += 8 + size + (size & 1)
    if fmt is None or raw is None:
        raise ValueError("WAV without fmt or data")
    tag, ch, rate, _, _, bits = fmt
    if tag == 0xFFFE and len(fmt_body) >= 26:  # WAVE_FORMAT_EXTENSIBLE: the real format is in its sub-format
        tag = struct.unpack_from("<H", fmt_body, 24)[0]
    if ch < 1 or not 8000 <= rate <= 192000:
        raise ValueError(f"unusual WAV ({ch} channels, {rate} Hz)")
    if tag == 1 and bits == 16:
        x = np.frombuffer(raw[:len(raw) // 2 * 2], "<i2").astype(np.float32) / 32768
    elif tag == 1 and bits == 8:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif tag == 1 and bits == 24:
        b = np.frombuffer(raw[:len(raw) // 3 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        x = ((b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16) << 8 >> 8).astype(np.float32) / 8388608
    elif tag == 1 and bits == 32:
        x = np.frombuffer(raw[:len(raw) // 4 * 4], "<i4").astype(np.float32) / 2147483648
    elif tag == 3 and bits == 32:
        x = np.frombuffer(raw[:len(raw) // 4 * 4], "<f4").astype(np.float32)
    else:
        raise ValueError(f"WAV format {tag} / {bits} bit not supported")
    x = x[:len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    if len(x) / rate > MAX_SECONDS + 1:
        raise ValueError(f"longer than {MAX_SECONDS // 60} min")
    if rate != SR and len(x):
        n = int(round(len(x) * SR / rate))
        x = np.interp(np.arange(n) * (rate / SR), np.arange(len(x)), x).astype(np.float32)
    if not len(x):
        raise ValueError("no audio in the file")
    return np.nan_to_num(np.clip(x, -1, 1))


def wav_bytes(x):
    """float mono at SR -> 16-bit WAV bytes."""
    pcm = np.clip(np.round(np.asarray(x, np.float64) * 32768), -32768, 32767).astype("<i2").tobytes()  # 16-bit exact
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


class Store:
    """The recordings on disk plus their metadata in cfg.data["recordings"] ({id: meta}). `version` changes when
    the list or a name changes (pages re-render then)."""

    def __init__(self, cfg, folder=None):
        self.cfg = cfg
        self.folder = Path(folder or Path(cfg.folder) / "recordings")
        self.lock = threading.RLock()
        self.version = 0

    def _meta(self):
        with self.cfg.lock:
            recs = self.cfg.data.get("recordings")
            if not isinstance(recs, dict):
                recs = self.cfg.data["recordings"] = {}
            return recs

    def path(self, rid):
        if not ID_RE.match(str(rid)):
            raise KeyError(rid)
        return self.folder / f"{rid}.wav"

    def list(self):
        """[meta with "id"], oldest first: every file on disk, with its settings (defaults if a restored or reset
        config has none) checked and completed."""
        with self.lock:
            recs = self._meta()
            files = {p.stem: p for p in self.folder.glob("*.wav") if ID_RE.match(p.stem)} if self.folder.is_dir() else {}
            out = []
            for rid, p in files.items():
                m = recs.get(rid)
                if not isinstance(m, dict):
                    m = recs[rid] = defaults(rid, self._duration(p))
                out.append({"id": rid, **self._fix(m, p)})
            return sorted(out, key=lambda m: (m["added"], m["id"]))

    def meta(self, rid):
        """rid's live metadata (no disk access: for the audio thread), or None."""
        m = self._meta().get(rid)
        return m if isinstance(m, dict) and "speakers" in m else None

    def get(self, rid):
        return next((m for m in self.list() if m["id"] == rid), None)

    @staticmethod
    def _duration(p):
        try:
            return max(0, p.stat().st_size - 44) / 2 / SR
        except OSError:
            return 0.0

    def _fix(self, m, p):
        d = defaults(m.get("name") or "Recording", m.get("duration") or self._duration(p))
        m["name"] = clean_name(m.get("name") or d["name"])
        m["duration"] = d["duration"] if not isinstance(m.get("duration"), (int, float)) else m["duration"]
        m["level"] = min(150, max(0, m["level"])) if isinstance(m.get("level"), (int, float)) else 100
        m["loop"] = bool(m.get("loop", False))
        sp = m.get("speakers")
        m["speakers"] = [s for s in SPEAKERS if isinstance(sp, list) and s in sp] if isinstance(sp, list) else ["inside"]
        m["duck_db"] = min(0, max(-60, m["duck_db"])) if isinstance(m.get("duck_db"), (int, float)) else 0
        m["pinned"] = bool(m.get("pinned", False))
        m["added"] = m["added"] if isinstance(m.get("added"), (int, float)) else d["added"]
        return m

    def add(self, name, data, rid=None, meta=None):
        """Store WAV bytes as a new recording (or replace rid's audio); its meta. ValueError if unusable."""
        if len(data) > MAX_BYTES:
            raise ValueError(f"bigger than {MAX_BYTES // 1024 ** 2} MB")
        x = read_wav(data)
        rid = rid or secrets.token_hex(6)
        p = self.path(rid)
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(wav_bytes(x))
        os.replace(tmp, p)
        with self.lock:
            m = {**defaults(clean_name(name), len(x) / SR), **(meta or {})}
            m["duration"] = round(len(x) / SR, 2)
            self._meta()[rid] = self._fix(m, p)
            self.version += 1
        self.cfg.changed(0.2)
        return {"id": rid, **m}

    def update(self, rid, **fields):
        """Change name, level, loop, speakers, duck_db or pinned (checked)."""
        with self.lock:
            m = self._meta().get(rid)
            if m is None:
                raise KeyError(rid)
            m.update({k: v for k, v in fields.items() if k in ("name", "level", "loop", "speakers", "duck_db",
                                                               "pinned")})
            self._fix(m, self.path(rid))
            if "name" in fields or "pinned" in fields:
                self.version += 1
        self.cfg.changed(0.5)
        return m

    def delete(self, rid):
        with self.lock:
            try:
                self.path(rid).unlink()
            except FileNotFoundError:
                pass
            self._meta().pop(rid, None)
            self.version += 1
        self.cfg.changed(0.2)

    def zip_bytes(self):
        """All recordings as a zip: recordings.json (metadata) + <id>.wav. Written uncompressed (WAV hardly
        compresses; the Pi's CPU is better spent on audio)."""
        buf = io.BytesIO()
        recs = self.list()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            z.writestr("recordings.json", json.dumps({m["id"]: {k: v for k, v in m.items() if k != "id"}
                                                      for m in recs}, indent=2, ensure_ascii=False))
            for m in recs:
                z.write(self.path(m["id"]), f"{m['id']}.wav")
        return buf.getvalue()

    def restore_zip(self, data):
        """Add (or replace, by id) the recordings in a zip from zip_bytes(); how many. ValueError if not one."""
        if len(data) > MAX_ZIP:
            raise ValueError("zip too big")
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ValueError("not a zip file") from e
        with z:
            try:
                meta = json.loads(z.read("recordings.json"))
            except (KeyError, ValueError) as e:
                raise ValueError("not a recordings zip (no recordings.json)") from e
            if not isinstance(meta, dict):
                raise ValueError("recordings.json is not an object")
            n = 0
            for rid, m in meta.items():
                if not ID_RE.match(str(rid)) or not isinstance(m, dict):
                    continue
                try:
                    info = z.getinfo(f"{rid}.wav")
                except KeyError:
                    continue
                if info.file_size > MAX_BYTES:
                    continue
                self.add(m.get("name"), z.read(info), rid=rid, meta=m)
                n += 1
        return n


class Voice:
    def __init__(self, rid, samples):
        self.rid, self.x, self.pos = rid, samples, 0
        self.fade = 0  # samples into the fade-in
        self.stopping = -1  # samples into the fade-out, -1 = playing


class Player:
    """Plays recordings: several at once, each on its own speakers at its own level (both read live from the
    metadata, so the settings apply while it plays), looping or not, ducking the train on its speakers."""

    def __init__(self, store):
        self.store = store
        self.voices = {}  # id -> Voice
        self.lock = threading.Lock()
        self.duck = {sp: 1.0 for sp in SPEAKERS}

    def play(self, rid):
        """Start (or restart) a recording. KeyError if there is no such recording."""
        p = self.store.path(rid)
        if self.store.get(rid) is None:
            raise KeyError(rid)
        x = np.memmap(p, dtype="<i2", mode="r", offset=44)  # read as it plays: a 10 min file is 57 MB
        with self.lock:
            self.voices[rid] = Voice(rid, x)

    def stop(self, rid):
        with self.lock:
            v = self.voices.get(rid)
            if v and v.stopping < 0:
                v.stopping = 0

    def toggle(self, rid):
        with self.lock:
            v = self.voices.get(rid)
            playing = v is not None and v.stopping < 0
        self.stop(rid) if playing else self.play(rid)

    def stop_all(self):
        with self.lock:
            for v in self.voices.values():
                if v.stopping < 0:
                    v.stopping = 0

    def state(self):
        """{id: progress 0-1} of what plays now."""
        with self.lock:
            return {rid: (v.pos / len(v.x) if len(v.x) else 1.0) for rid, v in self.voices.items() if v.stopping < 0}

    def pull(self, n):
        """The next n samples: {speaker: (recordings mixed for it, train gain [n])} for every speaker a recording
        plays on, or ducks; {} when silent."""
        with self.lock:
            voices = list(self.voices.values())
        mix = {}
        target = {sp: 1.0 for sp in SPEAKERS}
        ramp = np.arange(n, dtype=np.float32)
        for v in voices:
            m = self.store.meta(v.rid)
            if m is None:  # deleted while playing
                self._drop(v)
                continue
            out = np.zeros(n, np.float32)
            got = 0
            while got < n:
                k = min(n - got, len(v.x) - v.pos)
                if k > 0:
                    out[got:got + k] = v.x[v.pos:v.pos + k]
                    v.pos += k
                    got += k
                if v.pos >= len(v.x):
                    if m["loop"] and len(v.x):
                        v.pos = 0
                    else:
                        break
            out /= 32768
            env = np.minimum((v.fade + ramp + 1) / FADE, 1.0)
            v.fade += n
            if v.stopping >= 0:
                env = env * np.maximum(1 - (v.stopping + ramp + 1) / FADE, 0.0)
                v.stopping += n
            out *= env * (m["level"] / 100)
            ended = v.stopping >= FADE or (v.pos >= len(v.x) and not m["loop"])
            for sp in m["speakers"]:
                mix[sp] = mix[sp] + out if sp in mix else out.copy()
                if not ended:
                    target[sp] = min(target[sp], 10 ** (m["duck_db"] / 20))
            if ended:
                self._drop(v)
        out = {}
        for sp in SPEAKERS:
            d, t = self.duck[sp], target[sp]
            if d == t == 1.0 and sp not in mix:
                continue
            g = d + np.sign(t - d) * np.minimum(abs(t - d), (ramp + 1) / DUCK_RAMP)  # full range in DUCK_RAMP
            self.duck[sp] = float(g[-1])
            out[sp] = (mix.get(sp, np.zeros(n, np.float32)), g)
        return out

    def _drop(self, v):
        with self.lock:
            if self.voices.get(v.rid) is v:
                del self.voices[v.rid]
