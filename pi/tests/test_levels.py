"""Level matching across sound cards: card volumes set in dB (fake amixer), old % settings migrated to the dB they
were at, per-card trim in the Router's gain and its soft limiter."""

import os
import stat

import numpy as np
import pytest

import caltreat.__main__ as m
from caltreat import config, hw

JACK_CGET = """numid=1,iface=MIXER,name='PCM Playback Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=-10239,max=400,step=0
  : values=-200
  | dBscale-min=-102.39dB,step=0.01dB,mute=1
"""
USB_CGET = """numid=6,iface=MIXER,name='Speaker Playback Volume'
  ; type=INTEGER,access=rw---R--,values=2,min=0,max=151,step=0
  : values=120,120
  | dBminmax-min=-28.37dB,max=-0.06dB
"""
USB_SGET = """Simple mixer control 'Speaker',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 151
  Mono:
  Front Left: Playback 120 [79%] [-5.68dB] [on]
  Front Right: Playback 120 [79%] [-5.68dB] [on]
"""


@pytest.fixture
def amixer(tmp_path, monkeypatch):
    """A fake amixer on PATH: logs its arguments, prints the C-Media or bcm2835 output."""
    (tmp_path / "jack.txt").write_text(JACK_CGET)
    (tmp_path / "usb.txt").write_text(USB_CGET)
    (tmp_path / "sget.txt").write_text(USB_SGET)
    exe = tmp_path / "amixer"
    exe.write_text(f"""#!/bin/sh
echo "$@" >> {tmp_path}/log
case "$*" in
  *cget*PCM*) cat {tmp_path}/jack.txt ;;
  *cget*) cat {tmp_path}/usb.txt ;;
  *sget*) cat {tmp_path}/sget.txt ;;
esac
""")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(hw, "FAKE", None)
    return lambda: (tmp_path / "log").read_text().splitlines()


def test_db_ranges_from_amixer(amixer):
    assert hw.db_range("Headphones", "PCM") == (-102.39, 4.0)  # dBscale: min + step x raw span
    assert hw.db_range("Device", "Speaker") == (-28.37, -0.06)  # dBminmax
    assert amixer() == ["-c Headphones cget name=PCM Playback Volume", "-c Device cget name=Speaker Playback Volume"]
    assert hw.get_volume_db("Device", "Speaker") == -5.68


def test_volumes_are_set_in_db_and_clamped(amixer):
    assert hw.set_volume_db(0, "Headphones", "PCM") == 0.0
    assert hw.set_volume_db(-3.25, "Device", "Speaker") == -3.25
    assert hw.set_volume_db(0, "Device", "Speaker") == -0.06  # the C-Media control tops out just below 0 dB
    assert hw.set_volume_db(9, "Headphones", "PCM") == 4.0
    sets = [a for a in amixer() if "sset" in a]
    assert sets == ["-c Headphones -q -- sset PCM 0.00dB", "-c Device -q -- sset Speaker -3.25dB",
                    "-c Device -q -- sset Speaker -0.06dB", "-c Headphones -q -- sset PCM 4.00dB"]


def test_apply_volumes_sets_every_card(amixer, monkeypatch):
    monkeypatch.setattr(hw, "usb_outputs", lambda: [("Device", "1.2")])
    monkeypatch.setitem(m.cfg.data, "card_db", {"usb:1.2": -6})
    m.apply_volumes()
    assert [a for a in amixer() if "sset" in a] == ["-c Headphones -q -- sset PCM 0.00dB",  # unset: full scale
                                                    "-c Device -q -- sset Speaker -6.00dB"]


def test_amixer_percent_maps_to_db_as_alsa_does():
    # alsa-utils volume_mapping.c: both ranges are wider than 24 dB, so the cubic-ish log mapping
    assert hw.pct_to_db(100, hw.JACK_DB) == 4.0 and hw.pct_to_db(100, hw.USB_DB) == -0.06
    assert hw.pct_to_db(80, hw.JACK_DB) == pytest.approx(-1.72, abs=0.02)
    assert hw.pct_to_db(80, hw.USB_DB) == pytest.approx(-3.76, abs=0.02)
    assert hw.pct_to_db(0, hw.USB_DB) == -28.37
    assert hw.pct_to_db(50, (-20, 0)) == -10  # a range up to 24 dB: linear in dB


def test_old_percent_volumes_migrate_to_db():
    old = {"version": 2, "volumes": {"jack": 80, "usb:1.2": 50},
           "speakers": {"a_ext": {"device": "jack", "ch": "both", "level": 70}}}
    assert config.validate(old) == []
    new = config.complete(old)
    assert "volumes" not in new and new["trim_db"] == {}
    assert new["card_db"] == {"jack": hw.pct_to_db(80, hw.JACK_DB), "usb:1.2": hw.pct_to_db(50, hw.USB_DB)}
    assert new["speakers"]["a_ext"]["level"] == 70  # the owner's levels stay
    assert config.validate(new) == []


@pytest.mark.parametrize("bad,err", [
    ({"speakers": {"a_ext": {"device": "jack", "level": 401}}}, "level"),
    ({"card_db": {"jack": 20}}, "card_db"),
    ({"trim_db": {"usb:1.2": -30}}, "trim_db"),
    ({"trim_db": {"/etc": 0}}, "trim_db"),
])
def test_level_settings_are_checked(bad, err):
    assert any(e.startswith(f"speakers.a_ext.{err}" if err == "level" else err)
               for e in config.validate({"version": 2, **bad}))
    assert config.validate({"version": 2, "speakers": {"a_ext": {"device": "jack", "level": 400}},
                            "trim_db": {"jack": 12.5}}) == []


def test_limiter_is_transparent_below_the_knee_and_never_clips():
    x = np.linspace(-m.LIMIT_KNEE, m.LIMIT_KNEE, 101)
    assert np.array_equal(m.limit(x), x)
    y = m.limit(np.linspace(-4, 4, 2001))  # +12 dB on a full-scale signal
    assert np.abs(y).max() < 1.0 and np.all(np.diff(y) > 0)  # smooth and monotonic, under full scale
    assert m.limit(np.array([0.9]))[0] == pytest.approx(0.8 + 0.2 * np.tanh(0.5))


def test_router_gain_is_level_times_card_trim(monkeypatch):
    monkeypatch.setattr(hw, "usb_outputs", lambda: [("Device", "1.2")])
    d = config.defaults()
    d["speakers"] = {"a_ext": {"device": "jack", "ch": "both", "level": 200},
                     "a_int": {"device": "usb:1.2", "ch": "right", "level": 100},
                     "b_ext": {"device": "usb:1.2", "ch": "left", "level": 50},
                     "b_int": {"device": "none", "ch": "both", "level": 100}}
    d["trim_db"] = {"usb:1.2": -6}

    class Cfg:
        data = d

        def on_change(self, f):
            pass

    r = m.Router(None, None, Cfg())
    monkeypatch.setattr(m, "Device", lambda key, card: type("D", (), {"card": card, "stop": lambda s: None})())
    r._plan()
    gains = {sp: g for p in r.plan.values() for sp, _, g in p["outs"]}
    assert gains["a_ext"] == pytest.approx(2 * 10 ** (hw.JACK_TRIM_DB / 20))  # default jack trim
    assert gains["a_int"] == pytest.approx(10 ** (-6 / 20))
    assert gains["b_ext"] == pytest.approx(0.5 * 10 ** (-6 / 20))


@pytest.mark.parametrize("version", [1, 2, 3])
def test_volumes_migrate_from_any_older_version(version):
    old = {"version": version, "volumes": {"jack": 100, "usb:1.3": 80}}
    assert config.validate(old) == []
    new = config.complete(old)
    assert new["version"] == config.VERSION == 4 and "volumes" not in new and "relay" not in new
    assert new["card_db"] == {"jack": 4.0, "usb:1.3": hw.pct_to_db(80, hw.USB_DB)}
    assert config.validate(new) == []
