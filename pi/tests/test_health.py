"""health.py (loop lag + watchdog, the Pi's stats from /proc and /sys, sound counters), health_ui.lines, speedups.py
(NiceGUI's cached signature check), the fake sound card's underrun reports and the players' xrun counting."""

import asyncio
import functools
import subprocess
import sys
import time
import types

import pytest

from caltreat import health, health_ui, speedups


def blocking_call():
    time.sleep(0.4)  # what a slow amixer / disk write / page build does to the loop


def test_loop_lag_is_measured_and_the_blocking_call_named(capsys):
    mon = health.Monitor()

    async def main():
        mon.start()
        await asyncio.sleep(0.2)
        blocking_call()
        await asyncio.sleep(0.2)
        mon.stop()

    asyncio.run(main())
    s = mon.snapshot()
    assert s["loop_lag_events"] == 1 and 350 < s["loop_lag_max_ms"] < 1000
    assert "blocking_call" in s["loop_last_block"]["where"]
    err = capsys.readouterr().err
    assert "caltreat: event loop blocked" in err and "test_health.py" in err and "blocking_call" in err


def test_limiter_counts_what_it_holds_back(capsys):
    lim = health.Limiter(every=0.2)
    assert lim("k", "one") and not lim("k", "two") and not lim("k", "three")
    time.sleep(0.25)
    assert lim("k", "four")
    err = capsys.readouterr().err
    assert "one" in err and "two" not in err and "four (+2 more since the last warning)" in err


def fake_pi(root, idle, total, carrier=4, throttled="50005"):
    (root / "proc/self").mkdir(parents=True, exist_ok=True)
    busy = total - idle
    (root / "proc/stat").write_text(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 {busy} 0 0 0 0 0 0 0 0 0\n"
                                    f"cpu1 0 0 0 {total} 0 0 0 0 0 0\n")
    (root / "proc/self/stat").write_text("1 (python) S " + " ".join(["0"] * 10) + f" {busy // 4} 0 0\n")
    (root / "proc/meminfo").write_text("MemTotal: 4000000 kB\nMemAvailable: 1024000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    (root / "proc/loadavg").write_text("1.00 0.50 0.20 2/300 999\n")
    for d, v in (("sys/class/thermal/thermal_zone0", {"temp": "61234"}),
                 ("sys/devices/system/cpu/cpu0/cpufreq", {"scaling_cur_freq": "1500000"}),
                 ("sys/devices/platform/soc/soc:firmware", {"get_throttled": throttled}),
                 ("sys/class/net/uap0", {"carrier_changes": str(carrier), "operstate": "up"})):
        (root / d).mkdir(parents=True, exist_ok=True)
        for k, t in v.items():
            (root / d / k).write_text(t + "\n")


def test_pi_stats_power_wifi_and_underrun_warnings(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "nobin"))  # no vcgencmd: the firmware file is read instead
    cards = {"usb:1.2": {"card": "Device", "ok": True, "underruns": 0, "xruns": 0, "dropped": 0}}
    mon = health.Monitor(lambda: {"cards": {k: dict(v) for k, v in cards.items()}}, sys_root=str(tmp_path))
    fake_pi(tmp_path, idle=1000, total=1000)
    mon._sample()
    fake_pi(tmp_path, idle=1050, total=1100, carrier=8)  # cpu0 fully busy for 100 ticks, cpu1 idle
    cards["usb:1.2"]["xruns"] = 3
    mon._sample()
    s = mon.snapshot()
    assert s["cpu_pct"] == 50.0 and s["cpu_core_max_pct"] == 100.0
    assert s["temp_c"] == 61.2 and s["arm_mhz"] == 1500 and s["mem_avail_mb"] == 1000
    assert s["throttled"] == 0x50005 and "undervoltage now" in s["throttled_text"]
    assert s["net"]["uap0"] == {"present": True, "up": True, "link_changes": 8, "link_changes_since_start": 4}
    assert s["net"]["wlan0"] == {"present": False}
    err = capsys.readouterr().err
    assert "UNDERVOLTAGE" in err and "sound card usb:1.2: 3 underruns" in err


def test_health_panel_lines():
    seen = {}
    h = {"cpu_pct": 40.0, "cpu_core_max_pct": 70.0, "temp_c": 55.0, "throttled": 0, "throttled_text": "ok",
         "loop_lag_ms": 2, "loop_lag_max_ms": 30, "loop_lag_events": 0,
         "cards": {"usb:1.2": {"card": "Device", "ok": True, "underruns": 2, "xruns": 1, "dropped": 0}},
         "engine": {"kind": "v12", "ready": True, "realtime": 3.1, "late": 0, "restarts": 0},
         "net": {"uap0": {"present": True, "up": True, "link_changes_since_start": 0}}, "mem_avail_mb": 900}
    rows = {r[0]: r for r in health_ui.lines(h, seen, now=100.0)}
    assert all(r[2] for r in rows.values()), rows  # first look: counters so far aren't news
    assert rows["Engine"][1].startswith("renders x3.1")
    h["cards"]["usb:1.2"]["underruns"] = 5
    h["loop_lag_events"] = 1
    h["loop_last_block"] = {"ms": 800, "where": "config.py:409 _write < __main__.py:10 save"}
    h["throttled"], h["throttled_text"] = 0x50005, "undervoltage now"
    h["net"]["uap0"]["link_changes_since_start"] = 4
    rows = {r[0]: r for r in health_ui.lines(h, seen, now=110.0)}
    assert not rows["usb:1.2"][2] and not rows["Web"][2] and not rows["Power"][2] and not rows["Serves"][2]
    assert "800 ms in config.py:409 _write" in rows["Web"][1] and "2 drops" in rows["Serves"][1]
    rows = {r[0]: r for r in health_ui.lines(h, seen, now=200.0)}  # a minute without new ones: fine again
    assert rows["usb:1.2"][2] and rows["Web"][2]


class Thing:
    def handler(self, e):
        pass

    def no_args(self):
        pass


def test_speedups_cache_answers_like_nicegui():
    orig = speedups._orig
    cases = [lambda: None, lambda e: None, lambda e=1: None, lambda *a: None, lambda *, k: None, lambda *, k=1: None,
             Thing().handler, Thing().no_args, functools.partial(lambda a, b: None, 1), print]
    for _ in range(2):  # second round from the cache
        for f in cases:
            assert speedups.expects_arguments(f) == orig(f), f
    # one code object, different defaults: different answers
    makers = [(lambda: (lambda x: None)), (lambda: (lambda x=2: None))]
    assert [speedups.expects_arguments(mk()) for mk in makers] == [True, False]

    @functools.wraps(lambda e: None)
    def wrapped():
        pass
    assert speedups.expects_arguments(wrapped) == orig(wrapped)


def test_fake_card_reports_underruns_like_aplay():
    p = subprocess.Popen([sys.executable, "-m", "caltreat.fakesink", "-", "t", "48000", "2", "20"],
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    block = b"\0" * 4096  # ~21 ms
    for _ in range(5):
        p.stdin.write(block)
        p.stdin.flush()
    time.sleep(0.25)  # the writer stalls: the card runs dry
    p.stdin.write(block)
    p.stdin.close()
    err = p.stderr.read().decode()
    p.wait(5)
    assert "underrun!!! (at least" in err


def test_player_xruns_are_counted():
    import caltreat.__main__ as m
    d = types.SimpleNamespace(xruns=0, errs=__import__("collections").deque(maxlen=20))
    proc = types.SimpleNamespace(stderr=iter([b"underrun!!! (at least 12.000 ms long)\n", b"\n",
                                              b"aplay: main:831: audio open error: No such device\n",
                                              b"underrun!!! (at least 3.000 ms long)\n"]))
    m.Device._read_errs(d, proc)
    assert d.xruns == 2 and list(d.errs) == ["aplay: main:831: audio open error: No such device"]


def test_priority_needs_the_limit(capsys):
    health._prio["ok"] = None
    import threading
    ok = health.raise_priority(threading.get_native_id())
    if ok:
        pytest.skip("this machine allows nice -10 (root or a raised limit)")
    assert "setup/network.sh" in capsys.readouterr().err
    assert not health.raise_priority(threading.get_native_id())
    assert capsys.readouterr().err == ""  # said once
