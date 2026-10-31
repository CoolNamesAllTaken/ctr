"""The Pi's networks for the settings page, read-only and without sudo (nmcli and iw): the Wi-Fi it joins (wlan0),
its own Wi-Fi for phones (uap0, "Caltreat Cab") and the boards' wired network (eth0). Names come from the setup
script's /etc/caltreat/network.conf (pi/setup/network.sh) when it's there.

    network.status()  # {"nm": bool, "sta": {...}, "ap": {...}, "boards": {...}}
"""

import os
import re
import subprocess
import time
from pathlib import Path

CONF = Path(os.environ.get("CALTREAT_NETWORK_CONF", "/etc/caltreat/network.conf"))
DEFAULTS = {"STA_IFACE": "wlan0", "STA_SSID": "caltreat", "AP_IFACE": "uap0", "AP_CON": "caltreat-ap",
            "AP_SSID": "Caltreat Cab", "AP_IP": "10.42.0.1", "BOARD_IFACE": "eth0"}


def read_conf(path=CONF):
    """network.conf (shell variables, values maybe quoted by printf %q) over the defaults."""
    out = dict(DEFAULTS)
    try:
        text = Path(path).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
        if m:
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] == "'":
                v = v[1:-1]
            elif v.startswith("$'") and v.endswith("'"):
                v = v[2:-1].encode().decode("unicode_escape")
            else:
                v = re.sub(r"\\(.)", r"\1", v)  # printf %q escapes: "Caltreat\ Cab"
            out[m.group(1)] = v
    return out


def terse(line):
    """Fields of one line of `nmcli -t` output (':' separates, '\\:' and '\\\\' are escaped)."""
    fields, cur, i = [], "", 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if c == ":":
            fields.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    fields.append(cur)
    return fields


def parse_devices(text):
    """`nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status` -> {device: {type, state, connection}}."""
    out = {}
    for line in text.splitlines():
        f = terse(line)
        if len(f) >= 4:
            out[f[0]] = {"type": f[1], "state": f[2], "connection": f[3]}
    return out


def parse_wifi_list(text):
    """`nmcli -t -f ACTIVE,SIGNAL,CHAN,SSID device wifi list ...` -> the active network {ssid, signal, chan}."""
    for line in text.splitlines():
        f = terse(line)
        if len(f) >= 4 and f[0] == "yes":
            return {"ssid": f[3], "signal": int(f[1]) if f[1].isdigit() else None,
                    "chan": int(f[2]) if f[2].isdigit() else None}
    return None


def parse_ip(text):
    """`nmcli -g IP4.ADDRESS device show X` ("10.42.0.1/24 | ...") -> "10.42.0.1"."""
    first = text.strip().split("|")[0].strip()
    return first.split("/")[0] if first else ""


def parse_iw_info(text):
    m = re.search(r"^\s*channel (\d+)", text, re.M)
    return int(m.group(1)) if m else None


def parse_stations(text):
    return sum(1 for line in text.splitlines() if line.startswith("Station "))


def _run(*cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def status(run=_run):
    c = read_conf()
    devs_text = run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")
    if devs_text is None:
        return {"nm": False, "conf": c}
    devs = parse_devices(devs_text)

    def dev(name):
        d = devs.get(name)
        if not d:
            return {"present": False, "state": "missing", "connection": "", "ip": ""}
        ip = parse_ip(run("nmcli", "-g", "IP4.ADDRESS", "device", "show", name) or "") \
            if d["state"].startswith("connected") else ""
        return {"present": True, "state": d["state"], "connection": d["connection"], "ip": ip}

    sta = dev(c["STA_IFACE"])
    sta["want"] = c["STA_SSID"]
    if sta["state"].startswith("connected"):
        sta.update(parse_wifi_list(run("nmcli", "-t", "-f", "ACTIVE,SIGNAL,CHAN,SSID", "device", "wifi", "list",
                                       "ifname", c["STA_IFACE"], "--rescan", "no") or "") or {})
    ap = dev(c["AP_IFACE"])
    ap["ssid"] = c["AP_SSID"]
    if ap["present"]:
        ap["chan"] = parse_iw_info(run("iw", "dev", c["AP_IFACE"], "info") or "")
        st = run("iw", "dev", c["AP_IFACE"], "station", "dump")
        ap["clients"] = parse_stations(st) if st is not None else None
    ap["setup"] = CONF.exists()
    boards = dev(c["BOARD_IFACE"])
    return {"nm": True, "sta": sta, "ap": ap, "boards": boards, "conf": c, "time": time.time()}


_cache = {"t": 0.0, "v": None}


def cached_status(max_age=4.0):
    """status(), shared by every open settings page for a few seconds."""
    if _cache["v"] is None or time.monotonic() - _cache["t"] > max_age:
        _cache["v"] = status()
        _cache["t"] = time.monotonic()
    return _cache["v"]
