"""The settings page's Network section (network.py): the Wi-Fi the Pi joins, its own Wi-Fi for phones, the
boards' wired network and the app's addresses. Read-only: names and passwords are changed with the setup script on
the Pi (pi/setup/network.sh), not from the page (anyone on the train's Wi-Fi can open the page).

    network_ui.section(servers)   # inside the settings page; servers: listen.Listeners
"""

from nicegui import run, ui

from . import network
from .lcfw_ui import el, upd

SETUP = "sudo ~/caltreat/setup/network.sh"
CSS = ".st.net { white-space: normal; overflow-wrap: anywhere; line-height: 1.35; }"


def lines(s, ports):
    """[(row name, text, ok, tooltip)] for a status() result and listen.Listeners.status()."""
    if not s:
        return [("Wi-Fi", "…", True, "")]
    if not s.get("nm"):
        return [("Wi-Fi", "NetworkManager isn't running (not a Pi?)", False, "")]
    out = []
    sta = s["sta"]
    if sta["state"].startswith("connected"):
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


def section(servers):
    ui.add_css(CSS)
    state = {"s": None}
    with el("section", "ctl set"):
        el("h2", "", "Network", "The Pi's Wi-Fi and wired networks (read-only)")
        rows = el("div", "rows")
        el("div", "mapdia", f"Wi-Fi names and passwords: on the Pi, {SETUP}")

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

    async def refresh():
        state["s"] = await run.io_bound(network.cached_status)
        draw()

    draw()
    ui.timer(5.0, refresh)
    ui.timer(0.1, refresh, once=True)
