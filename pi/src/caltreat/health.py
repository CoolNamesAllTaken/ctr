"""How the Pi and the app are doing: for /api/health, the Settings page's Health panel and pi/tools/diagnose.sh.

- Event loop lag: a task on the asyncio loop wakes every 50 ms and measures how late it is. A watchdog thread notices
  a loop that is stuck while it is stuck, notes where (the loop thread's Python stack), and the app logs one warning
  line when the loop lags more than LAG_WARN ("caltreat: event loop blocked 850 ms in config.py:409 _write ...").
  While the loop is stuck every page freezes and every NiceGUI update waits.
- Sound: underruns per card (the Router had no block for it: a gap), aplay's own xruns, dropped blocks; the engine's
  late blocks, restarts and render speed (realtime factor: how many times faster than real time it renders).
- The Pi: CPU, temperature, clock, undervoltage / throttling (vcgencmd), memory, Wi-Fi interfaces (access point up,
  how often its link went down: carrier changes).
All of it is read from /proc, /sys and vcgencmd in the watchdog thread, never on the loop.
"""

import asyncio
import os
import re
import subprocess
import sys
import threading
import time
import traceback

LAG_WARN = 0.2  # s: log a warning when the loop is this late
TICK = 0.05  # s between the loop task's wake-ups
WARN_EVERY = 10.0  # s: at most one warning of a kind per this (the rest are counted)
THROTTLE_BITS = {0: "undervoltage now", 1: "ARM clock capped now", 2: "throttled now", 3: "soft temperature limit now",
                 16: "undervoltage since boot", 17: "clock capped since boot", 18: "throttled since boot",
                 19: "soft temperature limit since boot"}


def log(msg):
    print(f"caltreat: {msg}", file=sys.stderr, flush=True)


class Limiter:
    """At most one log line per key every WARN_EVERY s; the lines in between are counted into the next one."""

    def __init__(self, every=WARN_EVERY):
        self.every, self.last, self.skipped = every, {}, {}

    def __call__(self, key, msg):
        now = time.monotonic()
        if now - self.last.get(key, -1e9) < self.every:
            self.skipped[key] = self.skipped.get(key, 0) + 1
            return False
        n = self.skipped.pop(key, 0)
        self.last[key] = now
        log(msg + (f" (+{n} more since the last warning)" if n else ""))
        return True


def where(frame, depth=4):
    """'file.py:123 fn < file.py:45 caller' for the innermost frames of a stack."""
    out = []
    for fs in reversed(traceback.extract_stack(frame)[-depth:]):
        out.append(f"{os.path.basename(fs.filename)}:{fs.lineno} {fs.name}")
    return " < ".join(out)


def throttle_text(v):
    if v is None:
        return "unknown"
    names = [n for b, n in THROTTLE_BITS.items() if v >> b & 1]
    return ", ".join(names) if names else "ok"


def _rd(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


class Monitor:
    """Started from the app's startup hook (on the loop). sources: callables returning dicts merged into the
    snapshot ("audio": Router + engine counters), called from the watchdog thread."""

    def __init__(self, audio_stats=None, sys_root="", net=("wlan0", "uap0")):
        self.audio_stats = audio_stats
        self.root = sys_root  # tests: a fake /proc + /sys
        self.net = net
        self.warn = Limiter()
        self.lag_ms = 0.0
        self.lag_max_ms = 0.0
        self.lag_events = 0  # lags over LAG_WARN
        self.last_block = None  # {"ms", "where", "ago"}
        self.beat = time.monotonic()
        self.loop_thread = None
        self.stuck_where = None
        self.sys = {}
        self.audio = {}
        self.carrier0 = {}
        self.running = False
        self._prev_cpu = None
        self._prev_proc = None
        self._last_vc = 0.0
        self._underruns = {}

    # ---------- the loop's side ----------
    def start(self):
        self.loop_thread = threading.get_ident()
        self.running = True
        self.beat = time.monotonic()
        self._task = asyncio.get_running_loop().create_task(self._ticker())
        threading.Thread(target=self._watch, name="caltreat-health", daemon=True).start()

    def stop(self):
        self.running = False

    async def _ticker(self):
        while self.running:
            t = time.monotonic()
            self.beat = t
            await asyncio.sleep(TICK)
            now = time.monotonic()
            lag = max(0.0, now - t - TICK)
            self.lag_ms = lag * 1000
            self.lag_max_ms = max(self.lag_max_ms, self.lag_ms)
            self.beat = now
            if lag > LAG_WARN:
                self.lag_events += 1
                at = self.stuck_where or "(not caught: under the watchdog's 100 ms look)"
                self.last_block = {"ms": round(self.lag_ms), "where": at, "at": time.time()}
                self.warn("lag", f"event loop blocked {self.lag_ms:.0f} ms (pages freeze meanwhile) in {at}")
            self.stuck_where = None

    # ---------- the watchdog thread ----------
    def _watch(self):
        last_sys = 0.0
        while self.running:
            time.sleep(0.1)
            stuck = time.monotonic() - self.beat
            if stuck > LAG_WARN * 0.75 and self.stuck_where is None:  # the loop is stuck now: where?
                f = sys._current_frames().get(self.loop_thread)
                if f is not None:
                    self.stuck_where = where(f)
            now = time.monotonic()
            if now - last_sys >= 1.0:
                last_sys = now
                try:
                    self._sample()
                except Exception as e:  # a surprise in /proc must never kill the watchdog
                    self.sys["error"] = str(e)

    def _sample(self):
        r = self.root
        s = dict(self.sys)
        # CPU (all cores) and this process
        cpu = {}
        for line in _rd(f"{r}/proc/stat").splitlines():
            if line.startswith("cpu"):
                p = line.split()
                v = list(map(int, p[1:9]))
                cpu[p[0]] = (sum(v), v[3] + v[4])
        if self._prev_cpu and "cpu" in cpu:
            pc = self._prev_cpu
            busy = {k: 100 * (1 - (cpu[k][1] - pc[k][1]) / max(1, cpu[k][0] - pc[k][0])) for k in cpu if k in pc}
            s["cpu_pct"] = round(busy.pop("cpu"), 1)
            s["cpu_core_max_pct"] = round(max(busy.values()), 1) if busy else s["cpu_pct"]
            ncore = max(1, len(busy))
            st = _rd(f"{r}/proc/self/stat")
            if st:
                f = st[st.rfind(")") + 2:].split()
                ticks = int(f[11]) + int(f[12])
                if self._prev_proc is not None:
                    total = (cpu["cpu"][0] - pc["cpu"][0]) / ncore  # ticks of one core
                    s["app_cpu_pct"] = round(100 * (ticks - self._prev_proc) / max(1, total), 1)
                self._prev_proc = ticks
        self._prev_cpu = cpu
        mem = dict(re.findall(r"^(\w+):\s+(\d+)", _rd(f"{r}/proc/meminfo"), re.M))
        if mem:
            s["mem_avail_mb"] = int(mem.get("MemAvailable", 0)) // 1024
            s["swap_used_mb"] = (int(mem.get("SwapTotal", 0)) - int(mem.get("SwapFree", 0))) // 1024
        s["load"] = _rd(f"{r}/proc/loadavg").split()[:3]
        t = _rd(f"{r}/sys/class/thermal/thermal_zone0/temp").strip()
        if t.lstrip("-").isdigit():
            s["temp_c"] = round(int(t) / 1000, 1)
        f = _rd(f"{r}/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").strip()
        if f.isdigit():
            s["arm_mhz"] = int(f) // 1000
        now = time.monotonic()
        if now - self._last_vc >= 5.0:  # vcgencmd: a process each time, so every 5 s
            self._last_vc = now
            v = self._throttled()
            if v is not None:
                if v & 1 and not s.get("throttled", 0) & 1:
                    log("UNDERVOLTAGE: the Pi's supply is too weak (needs 5.1 V 3 A; USB sound cards draw from it)")
                s["throttled"] = v
                s["throttled_text"] = throttle_text(v)
        # Wi-Fi: interface up, and how often its link dropped (carrier changes, since boot and since the app started)
        net = {}
        for ifc in self.net:
            base = f"{r}/sys/class/net/{ifc}"
            if not os.path.exists(base):
                net[ifc] = {"present": False}
                continue
            cc = _rd(f"{base}/carrier_changes").strip()
            cc = int(cc) if cc.isdigit() else 0
            self.carrier0.setdefault(ifc, cc)
            net[ifc] = {"present": True, "up": _rd(f"{base}/operstate").strip() == "up",
                        "link_changes": cc, "link_changes_since_start": cc - self.carrier0[ifc]}
        s["net"] = net
        self.sys = s
        if self.audio_stats:
            self.audio = self.audio_stats()
            for k, d in (self.audio.get("cards") or {}).items():
                u = d.get("underruns", 0) + d.get("xruns", 0)
                if u > self._underruns.get(k, u):
                    self.warn(f"underrun:{k}", f"sound card {k}: {u - self._underruns[k]} underruns in the last second "
                                               f"({d.get('underruns', 0)} gaps from the app, {d.get('xruns', 0)} from aplay "
                                               f"since start)")
                self._underruns[k] = u

    def _throttled(self):
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = _rd(f"{self.root}/sys/devices/platform/soc/soc:firmware/get_throttled")
            out = f"0x{out.strip()}" if out.strip() else ""
        m = re.search(r"0x([0-9a-fA-F]+)", out)
        return int(m.group(1), 16) if m else None

    def snapshot(self):
        return {"loop_lag_ms": round(self.lag_ms, 1), "loop_lag_max_ms": round(self.lag_max_ms, 1),
                "loop_lag_events": self.lag_events, "loop_last_block": self.last_block, **self.sys, **self.audio}
