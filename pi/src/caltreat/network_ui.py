"""The settings page's Network section (network.py): the Wi-Fi the Pi joins, its own Wi-Fi for phones, the
boards' wired network and the app's addresses. Names and passwords are changed with the setup script on the Pi
(pi/setup/network.sh), not from the page (anyone on the train's Wi-Fi can open the page). The one switch here:
"Join caltreat" on / off (off = access point only, the most stable on the built-in Wi-Fi's one radio).

    network_ui.section(servers)   # inside the settings page; servers: listen.Listeners
"""

from nicegui import background_tasks, run, ui

from . import network
from .lcfw_ui import btn, el, upd

SETUP = network.SETUP
STA_TIP = ("Off: the Pi only serves its own Wi-Fi (access point only). On the built-in Wi-Fi's one radio that is "
           "the most stable: no scans or roams of the joined Wi-Fi taking the radio from the phones, no access point "
           "channel moves. A laptop on “{ssid}” then loses its connection to the Pi: join “{ap}” or use the "
           "ethernet cable instead. Stays off after a reboot.")
CSS = ".st.net { white-space: normal; overflow-wrap: anywhere; line-height: 1.35; }"


def lines(s, ports):
    """[(row name, text, ok, tooltip)] for a status() result and listen.Listeners.status()."""
    if not s:
        return [("Wi-Fi", "…", True, "")]
    if not s.get("nm"):
        return [("Wi-Fi", "NetworkManager isn't running (not a Pi?)", False, "")]
    out = []
    sta = s["sta"]
    if sta.get("off"):
        out.append(("Joins", f"off: access point only (not joining “{sta['want']}”)", True,
                    "Turned off below (Join): the most stable on the one radio"))
    elif sta["state"].startswith("connected"):
        bits = [sta.get("ssid") or sta["connection"], sta["ip"]]
        if sta.get("signal") is not None:
            bits.append(f"{sta['signal']}%")
        if sta.get("chan"):
            bits.append(f"ch {sta['chan']}")
        out.append(("Joins", " · ".join(b for b in bits if b), True, "The Wi-Fi the Pi is a client of (wlan0)"))
    else:
        out.append(("Joins", f"looking for “{sta['want']}” ({sta['state']})", False,
                    "Not connected: NetworkManager keeps trying while it's out of range. The Pi's own Wi-Fi "
                    "works without it"))
    ap = s["ap"]
    if ap["state"].startswith("connected"):
        bits = [ap["ssid"], ap["ip"]]
        if ap.get("clients") is not None:
            bits.append(f"{ap['clients']} phone{'s' if ap['clients'] != 1 else ''}")
        if ap.get("chan"):
            bits.append(f"ch {ap['chan']}")
        out.append(("Serves", " · ".join(b for b in bits if b), True,
                    "The Pi's own Wi-Fi for phones on the train: join it, open caltreat.local"))
    elif not ap["setup"]:
        out.append(("Serves", f"not set up: {SETUP}", False, "The Pi's own Wi-Fi isn't set up yet (README, Network)"))
    else:
        out.append(("Serves", f"{ap['ssid']} is down ({ap['state']})", False,
                    "journalctl -t caltreat-ap; nmcli connection up caltreat-ap"))
    b = s["boards"]
    out.append(("Boards", f"{b['ip'] or '—'} ({b['state']})", b["state"].startswith("connected"),
                "The wired network for the lighting controllers (eth0)"))
    bad = [f"{p}" for p, why in sorted(ports.items()) if why]
    if bad:
        out.append(("Ports", "not on " + ", ".join(bad), False, " · ".join(w for w in ports.values() if w)))
    else:
        out.append(("Ports", " · ".join(str(p) for p in sorted(ports)), True,
                    "http://caltreat.local/ (80) and https://caltreat.local/ (443) work without a port"))
    return out


def switch_state(s, busy=False):
    """(button text, css class, tooltip, enabled) for the Join switch."""
    if not s or not s.get("nm"):
        return "…", "sbtn", "Reading the network…", False
    sta, c = s["sta"], s.get("conf", {})
    tip = STA_TIP.format(ssid=sta["want"], ap=c.get("AP_SSID", "Caltreat Cab"))
    if busy:
        return "…", "sbtn", "Switching…", False
    if not sta.get("toggle"):
        return ("○ off" if sta.get("off") else "● on"), "sbtn", \
            f"Needs the one-time setup on the Pi (it allows this switch): {SETUP}", False
    if sta.get("off"):
        return "○ off", "sbtn", "Turn on: join “" + sta["want"] + "” again. " + tip, True
    return "● on", "sbtn on", "Turn off: access point only. " + tip, True


def section(servers):
    ui.add_css(CSS)
    state = {"s": None, "busy": False, "msg": ""}
    with el("section", "ctl set"):
        el("h2", "", "Network", "The Pi's Wi-Fi and wired networks")
        rows = el("div", "rows")
        with el("div", "srow"):
            jn = el("div", "nm", "Join")
            with el("div", "ctlz"):
                sw = btn("…", lambda: toggle(), "Join the Wi-Fi")
                msg = el("span", "st net")
        el("div", "mapdia", f"Wi-Fi names and passwords: on the Pi, {SETUP}")

    async def switch(on):
        state["busy"] = True
        draw()
        ok, text = await run.io_bound(network.set_sta, on)
        state["busy"], state["msg"] = False, ("" if ok else "✗ " + text)
        await refresh()

    def toggle():
        s = state["s"]
        if state["busy"] or not s or not s.get("nm") or not s["sta"].get("toggle"):
            return
        if s["sta"].get("off"):
            background_tasks.create(switch(True), name="sta-on")
            return
        c = s.get("conf", {})
        with ui.dialog() as d, el("div", "dlg"):
            el("b", "", f"Stop joining “{s['sta']['want']}”? Access point only.")
            el("div", "mapdia", f"More stable on the one radio. A laptop on “{s['sta']['want']}” loses its "
                                f"connection to the Pi: join “{c.get('AP_SSID', 'Caltreat Cab')}” or use the "
                                "ethernet cable. Stays off after a reboot; turn it back on here.")
            with el("div", "ctlz"):
                btn("○ Turn off", lambda: (d.close(), background_tasks.create(switch(False), name="sta-off")),
                    "Access point only", "sbtn warn")
                btn("✕", d.close, "Cancel")
        d.open()

    shown = []

    def draw():
        ls = lines(state["s"], servers.status())
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
        s = state["s"]
        text, cls, tip, enabled = switch_state(s, state["busy"])
        want = (s or {}).get("sta", {}).get("want") or "caltreat"
        upd(jn, text=f"Join {want}")
        upd(sw, text=text, cls=cls, title=tip, disabled=not enabled)
        upd(msg, text=state["msg"], title=state["msg"] or " ", cls="st net bad", visible=bool(state["msg"]))

    async def refresh():
        state["s"] = await run.io_bound(network.cached_status)
        draw()

    draw()
    ui.timer(5.0, refresh)
    ui.timer(0.1, refresh, once=True)
