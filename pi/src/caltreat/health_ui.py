"""The settings page's Health panel (health.py): CPU, temperature, power, the event loop, the sound cards and the
engine, Wi-Fi. Read-only; refreshed every 2 s from the monitor's last sample (no work on the loop).

    health_ui.section(monitor)   # inside the settings page
"""

import time

from nicegui import ui

from .lcfw_ui import el, upd

RECENT = 60.0  # s: a counter that went up this recently is shown as a problem


def lines(h, seen=None, now=None):
    """[(row name, text, ok, tooltip)] for a Monitor.snapshot(). seen: {key: (value, when it last went up)}, kept
    between calls, so a counter is flagged only while it is going up."""
    seen = {} if seen is None else seen
    now = time.monotonic() if now is None else now

    def rising(key, v):
        old = seen.get(key)
        if old is None:
            seen[key] = (v, -1e9)
        elif v > old[0]:
            seen[key] = (v, now)
        return now - seen[key][1] < RECENT

    out = []
    if "cpu_pct" in h:
        bits = [f"CPU {h['cpu_pct']:.0f}% (busiest core {h.get('cpu_core_max_pct', 0):.0f}%)"]
        if "app_cpu_pct" in h:
            bits.append(f"app {h['app_cpu_pct']:.0f}%")
        if "temp_c" in h:
            bits.append(f"{h['temp_c']:.0f} °C")
        if "arm_mhz" in h:
            bits.append(f"{h['arm_mhz']} MHz")
        out.append(("Pi", " · ".join(bits), h["cpu_pct"] < 90 and h.get("temp_c", 0) < 80,
                    "CPU of all cores (app: this app's own share of one core), temperature, ARM clock"))
    if "throttled" in h:
        v = h["throttled"]
        out.append(("Power", h.get("throttled_text", "?") + f" (0x{v:x})", v == 0,
                    "vcgencmd get_throttled. Undervoltage: the supply is too weak (a Pi 4 needs 5.1 V 3 A; the USB "
                    "sound cards draw from it): sound glitches, USB resets, Wi-Fi drops"))
    lb = h.get("loop_last_block")
    txt = f"lag {h.get('loop_lag_ms', 0):.0f} ms, max {h.get('loop_lag_max_ms', 0):.0f} ms"
    n = h.get("loop_lag_events", 0)
    if n:
        txt += f" · {n} freeze{'s' if n != 1 else ''} > 200 ms"
        if lb:
            txt += f", last {lb['ms']} ms in {lb['where'].split(' < ')[0]}"
    out.append(("Web", txt, not rising("loop", n),
                "How late the web side's event loop runs. While it is stuck, every page freezes" +
                (f". Last: {lb['where']}" if lb else "")))
    for k, c in sorted((h.get("cards") or {}).items()):
        gaps = c.get("underruns", 0) + c.get("xruns", 0)
        txt = (f"{c.get('card')} · {'playing' if c.get('ok') else 'not playing'} · {c.get('underruns', 0)} gaps · "
               f"{c.get('xruns', 0)} underruns · {c.get('dropped', 0)} dropped")
        out.append((k, txt, bool(c.get("ok")) and not rising(f"card:{k}", gaps),
                    "Since the app started. Gaps: the app had no sound ready in time. Underruns: the card itself ran "
                    "dry (aplay). Dropped: blocks skipped to keep cards in step (a few are normal)"))
    e = h.get("engine") or {}
    if e.get("kind") == "v12":
        rt = e.get("realtime")
        txt = (f"renders x{rt} real time" if rt else "starting…") + \
               f" · {e.get('late', 0)} late blocks · {e.get('restarts', 0)} restarts"
        out.append(("Engine", txt, bool(e.get("ready")) and (rt or 9) >= 1.5 and not rising("late", e.get("late", 0)),
                    "The v12 sound engine (node): how many times faster than real time its slowest bus rendered its "
                    "worst block in the last second (under 1.5: too little spare CPU), blocks not ready in time"))
    for ifc, name, what in (("uap0", "Serves", "The Pi's own Wi-Fi (access point)"),
                            ("wlan0", "Joins", "The Wi-Fi the Pi joins")):
        d = (h.get("net") or {}).get(ifc)
        if not d:
            continue
        if not d.get("present"):
            out.append((name, f"{ifc} missing", ifc != "uap0", what))
            continue
        drops = d.get("link_changes_since_start", 0) // 2
        out.append((name, f"{ifc} {'up' if d.get('up') else 'DOWN'} · {drops} drop{'s' if drops != 1 else ''} since "
                          f"the app started", d.get("up") and not rising(f"net:{ifc}", drops),
                    what + ": how often its link went down and up (an access point restart drops every phone)"))
    if "mem_avail_mb" in h:
        out.append(("Memory", f"{h['mem_avail_mb']} MB free · swap {h.get('swap_used_mb', 0)} MB",
                    h["mem_avail_mb"] > 150, "Available memory and swap in use"))
    return out


def section(monitor):
    seen = {}
    with el("section", "ctl set"):
        el("h2", "", "Health", "How the Pi and the app are doing (pi/tools/diagnose.sh records more)")
        rows = el("div", "rows")
    shown = []

    def draw():
        ls = lines(monitor.snapshot(), seen)
        if len(ls) != len(shown):
            rows.clear()
            shown.clear()
            with rows:
                for _ in ls:
                    with el("div", "srow"):
                        nm = el("div", "nm")
                        with el("div", "ctlz"):
                            st = el("span", "st net")
                    shown.append((nm, st))
        for (nm, st), (name, text, ok, tip) in zip(shown, ls):
            upd(nm, text=name)
            upd(st, text=text, title=tip or name, cls="st net" if ok else "st net bad")

    draw()
    ui.timer(2.0, draw)
