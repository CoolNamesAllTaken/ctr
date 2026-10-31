"""Uploaded recordings (caltreat.recordings): the store (WAV checks, metadata in the config, delete, zip round trip),
the player (speakers, level, loop, ducking the train) and the Router playing them on the right fake sound card."""

import io
import json
import os
import struct
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest

from caltreat import recordings as rec
from caltreat.config import Config, defaults

SR = rec.SR


def tone(f=1000, secs=0.5, amp=0.5, rate=SR, ch=1):
    t = np.arange(int(secs * rate)) / rate
    x = amp * np.sin(2 * np.pi * f * t)
    pcm = (np.repeat(x[:, None], ch, axis=1) * 32767).astype("<i2").tobytes()
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * 2 * ch, 2 * ch, 16) + b"data" +
            struct.pack("<I", len(pcm)) + pcm)


@pytest.fixture
def store(tmp_path):
    return rec.Store(Config(defaults(), tmp_path))


def test_add_stores_mono_48k_and_metadata(store, tmp_path):
    m = store.add("Spooky laugh.mp3", tone(secs=1.0, rate=44100, ch=2))
    assert m["name"] == "Spooky laugh" and m["duration"] == pytest.approx(1.0, abs=0.01)
    assert (m["level"], m["loop"], m["speakers"], m["duck_db"], m["pinned"]) == (100, False, ["inside"], 0, False)
    x = rec.read_wav(store.path(m["id"]).read_bytes())
    assert len(x) == pytest.approx(SR, abs=2) and abs(x).max() == pytest.approx(0.5, abs=0.01)
    store.cfg.flush()
    saved = json.loads((tmp_path / "config.json").read_text())["recordings"]
    assert saved[m["id"]]["name"] == "Spooky laugh"
    assert [r["id"] for r in store.list()] == [m["id"]]


@pytest.mark.parametrize("data, err", [(b"hello", "not a WAV"), (b"RIFF\0\0\0\0WAVE" + b"\0" * 40, "without fmt"),
                                       (tone(secs=0.01)[:44], "no audio")])
def test_bad_files_refused(store, data, err):
    with pytest.raises(ValueError, match=err):
        store.add("x", data)
    assert store.list() == []


def test_too_long_refused(store, monkeypatch):
    monkeypatch.setattr(rec, "MAX_SECONDS", 1)
    with pytest.raises(ValueError, match="longer than"):
        store.add("x", tone(secs=3))


def test_update_checks_values_and_delete(store):
    rid = store.add("a", tone())["id"]
    v = store.version
    m = store.update(rid, level=500, speakers=["rear", "bogus", "front"], duck_db=-90, loop=1, pinned=True,
                     name="  Boo  ")
    assert (m["level"], m["speakers"], m["duck_db"], m["loop"], m["pinned"], m["name"]) == \
        (150, ["front", "rear"], -60, True, True, "Boo")
    assert store.version > v
    store.delete(rid)
    assert store.list() == [] and not store.path(rid).exists()
    with pytest.raises(KeyError):
        store.path("../config")


def test_files_without_metadata_get_defaults(store):
    """A reset or restored config has no metadata for the files on disk: they are still listed."""
    rid = store.add("a", tone())["id"]
    store.cfg.data = defaults()
    assert [(r["id"], r["speakers"]) for r in store.list()] == [(rid, ["inside"])]


def test_zip_round_trip(store, tmp_path):
    a = store.add("A", tone(f=500))
    store.update(a["id"], loop=True, speakers=["front"], pinned=True)
    store.add("B", tone(f=700))
    z = store.zip_bytes()
    assert sorted(zipfile.ZipFile(io.BytesIO(z)).namelist()) == sorted(
        ["recordings.json"] + [f"{r['id']}.wav" for r in store.list()])
    other = rec.Store(Config(defaults(), tmp_path / "other"))
    assert other.restore_zip(z) == 2
    got = {r["name"]: r for r in other.list()}
    assert got["A"]["loop"] and got["A"]["speakers"] == ["front"] and got["A"]["pinned"] and got["A"]["id"] == a["id"]
    assert other.path(a["id"]).read_bytes() == store.path(a["id"]).read_bytes()
    with pytest.raises(ValueError, match="not a zip"):
        other.restore_zip(b"nope")
    with pytest.raises(ValueError, match="no recordings.json"):
        buf = io.BytesIO()
        zipfile.ZipFile(buf, "w").writestr("x.wav", tone())
        other.restore_zip(buf.getvalue())


def run(player, secs):
    out = {}
    for _ in range(int(secs * SR / 512)):
        for sp, (sig, g) in player.pull(512).items():
            out.setdefault(sp, ([], []))
            out[sp][0].append(sig)
            out[sp][1].append(g)
    return {sp: (np.concatenate(a), np.concatenate(b)) for sp, (a, b) in out.items()}


def test_player_speakers_level_duck_and_end(store):
    m = store.add("t", tone(amp=0.5, secs=0.5))
    store.update(m["id"], speakers=["front", "rear"], level=50, duck_db=-20)
    p = rec.Player(store)
    p.play(m["id"])
    assert p.state() == {m["id"]: 0.0}
    out = run(p, 1.0)
    assert set(out) == {"front", "rear"}
    sig, g = out["front"]
    assert abs(sig[2000:20000]).max() == pytest.approx(0.25, abs=0.01)  # 0.5 at 50 %
    assert g[5000] == pytest.approx(0.1, abs=1e-3)  # -20 dB while it plays
    assert abs(sig[int(0.52 * SR):]).max() == 0 and g[-1] == pytest.approx(1.0, abs=1e-3)  # ended, train back
    assert p.state() == {} and p.pull(512) == {}


def test_player_loop_and_stop_fades(store):
    m = store.add("t", tone(amp=0.5, secs=0.2))
    store.update(m["id"], loop=True)
    p = rec.Player(store)
    p.play(m["id"])
    sig, _ = run(p, 0.5)["inside"]
    assert abs(sig[int(0.4 * SR):int(0.5 * SR) - 600]).max() > 0.4  # still playing after 2 loops
    p.stop(m["id"])
    assert p.state() == {}
    tail = np.concatenate([s for s, _ in [p.pull(512)["inside"]]])
    assert abs(tail[-50:]).max() < 0.05  # faded out within 10 ms
    assert p.pull(512) == {}


def test_two_recordings_mix(store):
    a, b = store.add("a", tone(f=500, amp=0.3)), store.add("b", tone(f=900, amp=0.3))
    store.update(b["id"], speakers=["inside", "rear"])
    p = rec.Player(store)
    p.play(a["id"])
    p.play(b["id"])
    out = run(p, 0.3)
    spec = np.abs(np.fft.rfft(out["inside"][0][2000:14000]))
    f = np.fft.rfftfreq(12000, 1 / SR)
    peaks = sorted(f[np.argsort(spec)[-2:]])
    assert peaks == pytest.approx([500, 900], abs=5) and set(out) == {"inside", "rear"}
    p.stop_all()
    assert p.state() == {}


def test_router_plays_recording_on_its_speaker():
    """Through the app's Router to the fake sound cards (conftest.py: fake audio)."""
    import caltreat.__main__ as m

    fake = Path(os.environ["CALTREAT_FAKE_AUDIO"])
    m.cfg.data["direction"] = "A"
    m.cfg.data["speakers"] = {"a_ext": {"device": "jack", "ch": "both", "level": 100},
                              "a_int": {"device": "usb:1.3", "ch": "right", "level": 100},
                              "b_ext": {"device": "usb#1", "ch": "left", "level": 50},
                              "b_int": {"device": "none", "ch": "both", "level": 100}}
    store = rec.Store(m.cfg)
    r = store.add("t", tone(f=1000, amp=0.5, secs=3))
    store.update(r["id"], speakers=["rear"], level=100)
    router = m.Router(m.audio, m.intercom, m.cfg)
    router.recordings = player = rec.Player(store)
    router.start()
    try:
        time.sleep(0.5)
        player.play(r["id"])
        time.sleep(1.0)
    finally:
        router.stop()
        store.delete(r["id"])

    def latest(name):
        raw = sorted(fake.glob(f"{name}-*.raw"), key=lambda p: p.stat().st_mtime)[-1]
        x = np.frombuffer(raw.read_bytes()[: raw.stat().st_size // 4 * 4], dtype="<i2").reshape(-1, 2) / 32768.0
        return x[-int(0.5 * SR):]

    rear, front, inside = latest("usb-1.2"), latest("jack"), latest("usb-1.3")

    def at_1k(x):
        s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) / len(x) * 4
        return s[np.argmin(abs(np.fft.rfftfreq(len(x), 1 / SR) - 1000))]

    assert at_1k(rear[:, 0]) == pytest.approx(0.25, rel=0.1)  # rear: left channel, speaker level 50 %
    assert at_1k(rear[:, 1]) < 0.001 and at_1k(front[:, 0]) < 0.001 and at_1k(inside[:, 1]) < 0.001
