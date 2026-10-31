#!/usr/bin/env bash
# Caltreat health check: run it on the train's Pi while the app is in use (play sounds, open the pages on a phone,
# use the intercom). It watches the Pi for a few minutes and writes one file to send back:
#
#   bash ~/caltreat/tools/diagnose.sh          3 minutes (writes ~/caltreat-diag-<date>.tar.gz)
#   bash ~/caltreat/tools/diagnose.sh 300      5 minutes
#   sudo bash ~/caltreat/tools/diagnose.sh     the same, plus root-only logs (not needed on most Pis)
#
# Not on the Pi yet? From your computer (same network):
#   scp pi/tools/diagnose.sh caltreat@caltreat.local: && ssh -t caltreat@caltreat.local bash diagnose.sh
#   scp 'caltreat@caltreat.local:caltreat-diag-*.tar.gz' .
#
# What it records (no passwords: Wi-Fi keys are never read, and "psk" lines are removed):
#  - power and heat: undervoltage / throttling flags (now and since boot), temperature, CPU clock, over time
#  - CPU per process (the app, the sound engine node, aplay, NetworkManager, wpa_supplicant, dnsmasq) and per
#    thread for the app and node; load, memory, swap, SD-card busy time, I/O wait
#  - sound: each card's ALSA buffer fill (how close it came to running dry), the app's underrun counters
#  - the web side: how long the app takes to answer a request (a frozen event loop shows up here)
#  - Wi-Fi: iw / nmcli state, signal over time, phones on the access point, ping loss to the Wi-Fi's router,
#    and the logs (NetworkManager, wpa_supplicant, brcmfmac, the access point's channel helper, the app)
# The summary at the top of report.txt lists what looks wrong.
set -uo pipefail

DUR="${1:-180}"
if ! [[ "$DUR" =~ ^[0-9]+$ ]] || ((DUR < 10)); then echo "usage: $0 [seconds (10 or more), default 180]" >&2; exit 2; fi
PORT="${CALTREAT_PORT:-8080}"
STA="${STA_IFACE:-wlan0}"
AP="${AP_IFACE:-uap0}"
if [[ -r /etc/caltreat/network.conf ]]; then
  STA="$(sed -n 's/^STA_IFACE=//p' /etc/caltreat/network.conf | head -n1)"; STA="${STA:-wlan0}"
  AP="$(sed -n 's/^AP_IFACE=//p' /etc/caltreat/network.conf | head -n1)"; AP="${AP:-uap0}"
fi
HOME_DIR="$HOME"
if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then HOME_DIR="$(getent passwd "$SUDO_USER" | cut -d: -f6)"; fi
STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="caltreat-diag-$(hostname -s 2>/dev/null || echo pi)-$STAMP"
WORK="$(mktemp -d)"
OUT="$WORK/$NAME"
mkdir -p "$OUT"
trap 'rm -rf "$WORK"' EXIT
have() { command -v "$1" >/dev/null 2>&1; }
nopsk() { sed -E 's/((psk|password|wep-key[0-9]?|secret)[^=:]*[=:]).*/\1 <removed>/I'; }
# cap FILE CMD...: run a command into FILE (stderr too), never for more than 20 s
cap() {
  local f="$OUT/$1"; shift
  { echo "\$ $*"; timeout 20 "$@" 2>&1 | nopsk; echo; } >> "$f"
}

echo "Caltreat diagnostics: watching for $DUR s. Keep using the app (sounds, pages, intercom) meanwhile."
echo "(Ctrl-C stops early and still writes the file.)"

# ---------------------------------------------------------------- before: what the Pi is
{
  echo "date: $(date -Is)   uptime: $(uptime)"
  echo "model: $(tr -d '\0' 2>/dev/null < /proc/device-tree/model)"
  echo "kernel: $(uname -a)"
  grep -E '^(PRETTY_NAME)' /etc/os-release 2>/dev/null
  echo "user: $(id)"
  echo "duration: $DUR s   STA: $STA   AP: $AP   app port: $PORT"
  [[ -r "$HOME_DIR/caltreat/DEPLOYED_VERSION" ]] && echo "app: $(cat "$HOME_DIR/caltreat/DEPLOYED_VERSION")"
} > "$OUT/system.txt"
cap system.txt cat /proc/cpuinfo
cap system.txt free -m
cap system.txt df -h / /boot/firmware
cap system.txt cat /proc/swaps
if have vcgencmd; then
  cap power.txt vcgencmd get_throttled
  cap power.txt vcgencmd measure_volts core
  cap power.txt vcgencmd pmic_read_adc   # Pi 5 only: the 5 V input voltage (EXT5V_V)
  cap power.txt vcgencmd get_config int
fi
for f in /boot/firmware/config.txt /boot/config.txt; do [[ -r $f ]] && cap power.txt grep -v '^\s*#' "$f"; done
cap power.txt cat /sys/devices/platform/soc/soc:firmware/get_throttled
have lsusb && { cap usb.txt lsusb; cap usb.txt lsusb -t; }
cap sound.txt cat /proc/asound/cards
for f in /proc/asound/card*/pcm*p/sub0/hw_params; do [[ -r $f ]] && cap sound.txt cat "$f"; done
cap sound.txt aplay -l
if have systemctl; then
  cap app.txt systemctl --user status caltreat --no-pager
  cap app.txt systemctl --user show caltreat -p NRestarts -p ActiveEnterTimestamp -p ExecMainStartTimestamp \
    -p Nice -p CPUWeight -p IOWeight -p CPUSchedulingPolicy -p LimitRTPRIO -p LimitNICE -p MemoryCurrent
  cap app.txt systemctl --user cat caltreat
fi
cap app.txt ps -eLo pid,tid,cls,rtprio,ni,pri,psr,pcpu,stat,etime,comm,args --sort=-pcpu
cap app.txt curl -s -m 5 "http://127.0.0.1:$PORT/api/health"
cap system.txt ps -eo pid,ni,pcpu,pmem,rss,etime,comm,args --sort=-pcpu

net_snapshot() {  # $1: before / after
  local f="net-$1.txt"
  cap "$f" ip -br addr
  cap "$f" ip route
  if have iw; then
    cap "$f" iw dev
    cap "$f" iw dev "$STA" link
    cap "$f" iw dev "$STA" get power_save
    cap "$f" iw dev "$AP" get power_save
    cap "$f" iw dev "$STA" station dump
    cap "$f" iw dev "$AP" station dump
    cap "$f" iw reg get
  fi
  if have nmcli; then
    cap "$f" nmcli general
    cap "$f" nmcli dev
    cap "$f" nmcli -f NAME,UUID,TYPE,DEVICE,AUTOCONNECT,ACTIVE connection show
    cap "$f" nmcli -f GENERAL.STATE,802-11-wireless,ipv4.method connection show caltreat-ap
    cap "$f" nmcli -f IN-USE,BSSID,CHAN,RATE,SIGNAL,SECURITY,SSID dev wifi list ifname "$STA" --rescan no
  fi
  cap "$f" cat /proc/net/dev
  cap "$f" cat /proc/net/wireless
  if have systemctl; then
    cap "$f" systemctl status caltreat-uap0.service caltreat-ap-channel.timer caltreat-ap-channel.service --no-pager
  fi
  [[ -r /etc/caltreat/network.conf ]] && cap "$f" cat /etc/caltreat/network.conf
}
net_snapshot before

# ---------------------------------------------------------------- during: sample everything once a second
GW="$(ip route show default dev "$STA" 2>/dev/null | awk '{ print $3; exit }')"
[[ -z "$GW" ]] && GW="$(ip route show default 2>/dev/null | awk '{ print $3; exit }')"
PING_PID=""
if [[ -n "$GW" ]] && have ping; then
  echo "pinging the Wi-Fi's router $GW (from $STA)" > "$OUT/ping.txt"
  ping -D -i 1 -W 1 -w "$DUR" "$GW" >> "$OUT/ping.txt" 2>&1 &
  PING_PID=$!
else
  echo "no default route (not connected to a Wi-Fi with a router?)" > "$OUT/ping.txt"
fi
SINCE="$(date '+%Y-%m-%d %H:%M:%S')"
SAMPLER="$WORK/sampler.py"
cat > "$SAMPLER" <<'PY'
import json, os, re, shutil, subprocess, sys, threading, time, urllib.request

out, dur, port, sta, ap = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
HZ = os.sysconf("SC_CLK_TCK")
stop = [False]


def rd(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return ""


def run(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def cpu_times():
    cores = {}
    for line in rd("/proc/stat").splitlines():
        if line.startswith("cpu"):
            p = line.split()
            v = list(map(int, p[1:9]))
            cores[p[0]] = (sum(v), v[3] + v[4], v[4])  # total, idle+iowait, iowait
    return cores


def classify(cmd, comm):
    if "caltreat" in cmd and "python" in cmd and "fakesink" not in cmd:
        return "app"
    if "sidecar.js" in cmd:
        return "node"
    for k in ("aplay", "NetworkManager", "wpa_supplicant", "dnsmasq", "hostapd", "avahi-daemon", "systemd-journal",
              "Xorg", "chromium", "kworker", "irq/", "ksoftirqd", "brcmf"):
        if comm.startswith(k) or k in comm:
            return k.rstrip("/")
    return None


def procs():
    """{pid: (group, comm, ticks)} for the processes we care about, and their threads for app/node."""
    res, thr = {}, {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        st = rd(f"/proc/{pid}/stat")
        if not st:
            continue
        comm = st[st.find("(") + 1:st.rfind(")")]
        f = st[st.rfind(")") + 2:].split()
        ticks = int(f[11]) + int(f[12])
        cmd = rd(f"/proc/{pid}/cmdline").replace("\0", " ")
        g = classify(cmd, comm)
        if g is None:
            res[pid] = ("other", comm, ticks)
            continue
        res[pid] = (g, comm, ticks)
        if g in ("app", "node"):
            for tid in os.listdir(f"/proc/{pid}/task") if os.path.isdir(f"/proc/{pid}/task") else ():
                ts = rd(f"/proc/{pid}/task/{tid}/stat")
                if ts:
                    tc = ts[ts.find("(") + 1:ts.rfind(")")]
                    tf = ts[ts.rfind(")") + 2:].split()
                    thr[(g, pid, tid)] = (tc, int(tf[11]) + int(tf[12]))
    return res, thr


def disk():
    for line in rd("/proc/diskstats").splitlines():
        p = line.split()
        if len(p) > 13 and re.fullmatch(r"mmcblk\d|nvme\d+n\d|sd[a-z]", p[2]):
            return p[2], int(p[12]), int(p[7]), int(p[10])  # name, ms busy, writes done, ms writing
    return None, 0, 0, 0


def meminfo():
    m = {}
    for line in rd("/proc/meminfo").splitlines():
        k, v = line.split(":", 1)
        m[k] = int(v.split()[0])
    return m


def vmstat():
    return {k: int(v) for k, v in (l.split() for l in rd("/proc/vmstat").splitlines()) if k in
            ("pswpin", "pswpout", "pgmajfault")}


def psi():
    o = {}
    for r in ("cpu", "io", "memory"):
        m = re.search(r"some avg10=([\d.]+)", rd(f"/proc/pressure/{r}"))
        if m:
            o[r] = float(m.group(1))
    return o


def alsa():
    """Per playing card: state, buffer size and avail_max (frames). avail_max is the most the buffer was empty
    since the last read: near buffer_size means the card nearly ran dry."""
    o = {}
    base = "/proc/asound"
    for card in sorted(os.listdir(base)) if os.path.isdir(base) else ():
        if not re.fullmatch(r"card\d+", card):
            continue
        cid = rd(f"{base}/{card}/id").strip() or card
        for pcm in sorted(os.listdir(f"{base}/{card}")):
            if not re.fullmatch(r"pcm\d+p", pcm):
                continue
            s = rd(f"{base}/{card}/{pcm}/sub0/status")
            if "state:" not in s:
                continue
            hp = rd(f"{base}/{card}/{pcm}/sub0/hw_params")
            d = dict(re.findall(r"^(\w+)\s*:\s*(\S+)", s, re.M))
            h = dict(re.findall(r"^(\w+)\s*:\s*(\S+)", hp, re.M))
            o[f"{cid}/{pcm}"] = {"state": d.get("state"), "avail_max": int(d.get("avail_max", 0) or 0),
                                 "buffer": int(h.get("buffer_size", 0) or 0), "rate": int(h.get("rate", 48000) or 48000)}
    return o


def power():
    o = {}
    t = run("vcgencmd", "get_throttled")
    m = re.search(r"0x([0-9a-fA-F]+)", t) or re.search(r"([0-9a-fA-F]+)", rd(
        "/sys/devices/platform/soc/soc:firmware/get_throttled"))
    if m:
        o["throttled"] = int(m.group(1), 16)
    m = re.search(r"temp=([\d.]+)", run("vcgencmd", "measure_temp"))
    if m:
        o["temp"] = float(m.group(1))
    else:
        t = rd("/sys/class/thermal/thermal_zone0/temp").strip()
        if t:
            o["temp"] = int(t) / 1000
    m = re.search(r"=(\d+)", run("vcgencmd", "measure_clock", "arm"))
    if m:
        o["arm_mhz"] = int(m.group(1)) // 1000000
    else:
        f = rd("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").strip()
        if f:
            o["arm_mhz"] = int(f) // 1000
    return o


def wifi():
    o = {}
    if not shutil.which("iw"):
        return o
    link = run("iw", "dev", sta, "link")
    o["sta_connected"] = link.startswith("Connected")
    for k, pat in (("signal", r"signal:\s*(-?\d+)"), ("freq", r"freq:\s*([\d.]+)"),
                   ("tx_mbps", r"tx bitrate:\s*([\d.]+)"), ("rx_mbps", r"rx bitrate:\s*([\d.]+)")):
        m = re.search(pat, link)
        if m:
            o[k] = float(m.group(1))
    m = re.search(r"Connected to ([0-9a-f:]{17})", link)
    if m:
        o["bssid"] = m.group(1)
    info = run("iw", "dev", ap, "info")
    m = re.search(r"channel (\d+)", info)
    o["ap_channel"] = int(m.group(1)) if m else None
    m = re.search(r"channel (\d+)", run("iw", "dev", sta, "info"))
    o["sta_channel"] = int(m.group(1)) if m else None
    sd = run("iw", "dev", ap, "station", "dump")
    o["ap_phones"] = sd.count("Station ")
    o["ap_signal"] = [int(x) for x in re.findall(r"^\s*signal:\s*(-?\d+)", sd, re.M)]
    o["ap_tx_failed"] = sum(int(x) for x in re.findall(r"tx failed:\s*(\d+)", sd))
    o["ap_tx_retries"] = sum(int(x) for x in re.findall(r"tx retries:\s*(\d+)", sd))
    o["ap_inactive_ms"] = [int(x) for x in re.findall(r"inactive time:\s*(\d+)", sd)]
    return o


def netdev():
    o = {}
    for line in rd("/proc/net/dev").splitlines()[2:]:
        name, rest = line.split(":", 1)
        v = list(map(int, rest.split()))
        o[name.strip()] = {"rx": v[0], "rx_err": v[2], "rx_drop": v[3], "tx": v[8], "tx_err": v[10], "tx_drop": v[11]}
    return o


# the app: how long it takes to answer, in its own thread (a hung app must not stop the sampling)
probe = {"ms": None, "max_ms": 0.0, "fails": 0, "health": None, "pending_since": None}


def prober():
    paths = ["/api/health", "/api/intercom"]
    while not stop[0]:
        t = time.monotonic()
        probe["pending_since"] = t
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{paths[0]}", timeout=10) as r:
                body = r.read()
            ms = (time.monotonic() - t) * 1000
            probe["ms"] = ms
            try:
                probe["health"] = json.loads(body)
            except ValueError:
                pass
        except urllib.error.HTTPError as e:
            if e.code == 404 and len(paths) > 1:
                paths.pop(0)  # an older app without /api/health
                continue
            probe["fails"] += 1
            probe["ms"] = None
        except Exception:
            probe["fails"] += 1
            probe["ms"] = None
        probe["pending_since"] = None
        time.sleep(max(0.0, 1.0 - (time.monotonic() - t)))


threading.Thread(target=prober, daemon=True).start()
samples = open(os.path.join(out, "samples.jsonl"), "w")
prev_cpu, (prev_p, prev_t), prev_disk = cpu_times(), procs(), disk()
t0 = time.monotonic()
last_slow = 0
i = 0
try:
    while time.monotonic() - t0 < dur:
        time.sleep(1.0 - ((time.monotonic() - t0) % 1.0))
        i += 1
        now = time.monotonic()
        s = {"t": round(now - t0, 1), "wall": time.strftime("%H:%M:%S")}
        c = cpu_times()
        s["cpu"] = {k: round(100 * (1 - (c[k][1] - prev_cpu[k][1]) / max(1, c[k][0] - prev_cpu[k][0])), 1)
                    for k in c if k in prev_cpu}
        s["iowait"] = round(100 * (c["cpu"][2] - prev_cpu["cpu"][2]) / max(1, c["cpu"][0] - prev_cpu["cpu"][0]), 1)
        dt = (c["cpu"][0] - prev_cpu["cpu"][0]) / max(1, len(c) - 1)  # ticks per core in this second
        prev_cpu = c
        p, t = procs()
        groups = {}
        for pid, (g, comm, ticks) in p.items():
            if pid in prev_p:
                groups[g] = groups.get(g, 0) + ticks - prev_p[pid][2]
        s["proc"] = {g: round(100 * v / max(1, dt), 1) for g, v in groups.items() if v}
        s["threads"] = {f"{g}:{comm}:{tid}": round(100 * (ticks - prev_t[(g, pid, tid)][1]) / max(1, dt), 1)
                        for (g, pid, tid), (comm, ticks) in t.items()
                        if (g, pid, tid) in prev_t and ticks - prev_t[(g, pid, tid)][1] > 0}
        s["pids"] = {g: sorted({pid for pid, v in p.items() if v[0] == g}) for g in ("app", "node", "aplay")}
        prev_p, prev_t = p, t
        d = disk()
        s["disk"] = {"dev": d[0], "busy": round((d[1] - prev_disk[1]) / 10, 1), "writes": d[2] - prev_disk[2],
                     "write_ms": d[3] - prev_disk[3]}
        prev_disk = d
        s["load"] = rd("/proc/loadavg").split()[:3]
        m = meminfo()
        s["mem_avail_mb"] = m.get("MemAvailable", 0) // 1024
        s["swap_used_mb"] = (m.get("SwapTotal", 0) - m.get("SwapFree", 0)) // 1024
        s["vm"] = vmstat()
        s["psi"] = psi()
        s["alsa"] = alsa()
        if i % 2 == 1:
            s["power"] = power()
        if i % 3 == 1:
            s["wifi"] = wifi()
            s["net"] = netdev()
        s["http_ms"] = None if probe["ms"] is None else round(probe["ms"], 1)
        if probe["pending_since"] is not None and now - probe["pending_since"] > 1.0:
            s["http_pending_ms"] = round((now - probe["pending_since"]) * 1000)
        s["http_fails"] = probe["fails"]
        if probe["health"] is not None:
            s["app"] = probe["health"]
        samples.write(json.dumps(s) + "\n")
        samples.flush()
        left = dur - int(now - t0)
        if left % 30 == 0 and left:
            print(f"  {left} s left ...", flush=True)
except KeyboardInterrupt:
    print("  stopped early", flush=True)
stop[0] = True
samples.close()
PY
python3 -I "$SAMPLER" "$OUT" "$DUR" "$PORT" "$STA" "$AP"
[[ -n "$PING_PID" ]] && { kill "$PING_PID" 2>/dev/null; wait "$PING_PID" 2>/dev/null; }

# ---------------------------------------------------------------- after: logs and state again
net_snapshot after
if have vcgencmd; then cap power.txt vcgencmd get_throttled; fi
cap app.txt ps -eLo pid,tid,cls,rtprio,ni,pri,psr,pcpu,stat,etime,comm,args --sort=-pcpu
cap app.txt curl -s -m 5 "http://127.0.0.1:$PORT/api/health"
if have journalctl; then
  J=(journalctl --no-pager -o short-iso)
  cap journal-boots.txt journalctl --list-boots --no-pager
  # the app (its warnings: stalled / failed cards, underruns, slow event loop, engine restarts)
  cap journal-app.txt "${J[@]}" -b --user-unit caltreat.service -n 3000
  [[ -s "$OUT/journal-app.txt" ]] && grep -q -- '-- No entries --' "$OUT/journal-app.txt" &&
    cap journal-app.txt "${J[@]}" -b _SYSTEMD_USER_UNIT=caltreat.service -n 3000
  # Wi-Fi and the access point (system journal: the Pi's first user is in group adm, which may read it)
  cap journal-net.txt "${J[@]}" -b -n 4000 -u NetworkManager -u wpa_supplicant -u caltreat-ap-channel \
    -u caltreat-uap0 -u NetworkManager-dispatcher -u avahi-daemon -t caltreat-ap -t dnsmasq -t dnsmasq-dhcp
  # kernel: Wi-Fi driver, USB (sound cards dropping off), undervoltage, sound
  { echo "\$ journalctl -k -b (filtered)"; timeout 20 journalctl -k -b --no-pager -o short-iso 2>&1 |
    grep -Ei 'brcm|wlan|uap0|ieee80211|cfg80211|usb|snd|audio|xrun|volt|throttl|thermal|oom|mmc|error|fail|warn' |
    tail -n 3000; } > "$OUT/journal-kernel.txt"
  cap journal-kernel.txt journalctl -k -b -1 --no-pager -o short-iso -n 200   # the boot before (a brownout reboot?)
  cap journal-during.txt "${J[@]}" --since "$SINCE" -n 5000
fi
if [[ ! -s "$OUT/journal-kernel.txt" ]] || ! grep -q '[a-z]' "$OUT/journal-kernel.txt"; then
  cap journal-kernel.txt dmesg -T
fi

# ---------------------------------------------------------------- summary
python3 -I - "$OUT" "$DUR" "$STA" > "$OUT/summary.txt" <<'PY'
import json, os, re, sys

out, dur, sta = sys.argv[1], int(sys.argv[2]), sys.argv[3]
S = [json.loads(l) for l in open(os.path.join(out, "samples.jsonl")) if l.strip()]


def rd(n):
    try:
        return open(os.path.join(out, n), errors="replace").read()
    except OSError:
        return ""


warn, info = [], []
W = lambda s: warn.append("⚠ " + s)
I = lambda s: info.append("  " + s)
if not S:
    W("no samples recorded")
    print("\n".join(warn))
    sys.exit()

# power
th = [s["power"]["throttled"] for s in S if "throttled" in s.get("power", {})]
bits = 0
for v in th:
    bits |= v
names = {0: "UNDERVOLTAGE now", 1: "ARM clock capped now", 2: "THROTTLED now", 3: "soft temperature limit now",
         16: "undervoltage has occurred since boot", 17: "ARM clock capping has occurred since boot",
         18: "throttling has occurred since boot", 19: "soft temperature limit has occurred since boot"}
if th:
    flags = [n for b, n in names.items() if bits >> b & 1]
    if bits & 0x10001:
        W("UNDERVOLTAGE: " + "; ".join(flags) + f" (get_throttled 0x{bits:x}). The Pi 4 needs a 5.1 V 3 A supply; "
          "USB sound cards draw from it too. Undervoltage causes audio glitches, USB resets and Wi-Fi drops.")
    elif flags:
        W("; ".join(flags) + f" (get_throttled 0x{bits:x})")
    else:
        I(f"power: no undervoltage or throttling (get_throttled 0x{bits:x})")
    if any(v & 1 for v in th):
        W(f"undervoltage was present in {sum(1 for v in th if v & 1)} of {len(th)} power samples during the run")
else:
    I("power: vcgencmd get_throttled not available (not a Pi, or not in group video)")
temps = [s["power"]["temp"] for s in S if "temp" in s.get("power", {})]
clk = [s["power"]["arm_mhz"] for s in S if "arm_mhz" in s.get("power", {})]
if temps:
    (W if max(temps) >= 80 else I)(f"temperature {min(temps):.0f}-{max(temps):.0f} °C"
                                    + (" (throttles at 80-85 °C: add a heatsink / fan)" if max(temps) >= 80 else ""))
if clk:
    I(f"ARM clock {min(clk)}-{max(clk)} MHz")

# CPU
tot = [s["cpu"].get("cpu", 0) for s in S]
cores = [max(v for k, v in s["cpu"].items() if k != "cpu") for s in S if len(s["cpu"]) > 1]
avg = lambda xs: sum(xs) / max(1, len(xs))
I(f"CPU total avg {avg(tot):.0f}% max {max(tot):.0f}%; busiest core avg {avg(cores):.0f}% max {max(cores or [0]):.0f}%")
if max(tot) > 90:
    W(f"CPU saturated: total {max(tot):.0f}% at times ({sum(1 for v in tot if v > 90)} s above 90%)")
groups = {}
for s in S:
    for g, v in s.get("proc", {}).items():
        groups.setdefault(g, []).append(v)
line = ", ".join(f"{g} {avg(v) * len(v) / len(S):.0f}% (max {max(v):.0f}%)" for g, v in
                 sorted(groups.items(), key=lambda kv: -sum(kv[1])) if max(v) >= 1)
I("CPU by process (100% = one core): " + line)
thr = {}
for s in S:
    for k, v in s.get("threads", {}).items():
        thr.setdefault(k, []).append(v)
top = sorted(thr.items(), key=lambda kv: -sum(kv[1]))[:12]
I("busiest threads: " + ", ".join(f"{k} {sum(v) / len(S):.0f}% (max {max(v):.0f}%)" for k, v in top))
for k, v in thr.items():
    if max(v) >= 95:
        W(f"thread {k} used a whole core ({max(v):.0f}%) at times: anything else on that thread waits")
iow = [s["iowait"] for s in S]
if max(iow) > 10:
    W(f"I/O wait up to {max(iow):.0f}% (SD card slow / busy)")
busy = [s["disk"]["busy"] for s in S if s.get("disk", {}).get("dev")]
if busy:
    (W if max(busy) > 50 else I)(f"SD card busy max {max(busy):.0f}% of a second, avg {avg(busy):.1f}%")
swap = [s["swap_used_mb"] for s in S]
memav = [s["mem_avail_mb"] for s in S]
I(f"memory available min {min(memav)} MB; swap used max {max(swap)} MB")
sw = S[-1]["vm"].get("pswpin", 0) - S[0]["vm"].get("pswpin", 0) + S[-1]["vm"].get("pswpout", 0) - S[0]["vm"].get("pswpout", 0)
if sw > 100:
    W(f"swapping during the run ({sw} pages)")
if min(memav) < 150:
    W(f"low memory: only {min(memav)} MB available at times")
psi = {}
for s in S:
    for k, v in s.get("psi", {}).items():
        psi[k] = max(psi.get(k, 0), v)
if psi:
    I("pressure (PSI some avg10 max): " + ", ".join(f"{k} {v:.0f}%" for k, v in psi.items()))
pids = {}
for s in S:
    for g, ps in s.get("pids", {}).items():
        pids.setdefault(g, set()).update(ps)
for g, what in (("app", "the app"), ("node", "the sound engine (node)"),
                ("aplay", "aplay (a sound card stalled or dropped off)")):
    first, last = set(S[0]["pids"].get(g, [])), set(S[-1]["pids"].get(g, []))
    new, gone = pids.get(g, set()) - first, first - last
    if new and gone:  # one went away and another started
        W(f"{what} restarted during the run ({len(gone)} gone, {len(new)} started)")
    if not last and g != "aplay":
        W(f"{what} is not running")

# sound cards (ALSA)
cards = {}
for s in S:
    for k, v in s.get("alsa", {}).items():
        c = cards.setdefault(k, {"min_ms": 1e9, "xrun": 0, "n": 0, "buf_ms": 0})
        if v["buffer"]:
            c["buf_ms"] = v["buffer"] * 1000 / v["rate"]
            left = (v["buffer"] - v["avail_max"]) * 1000 / v["rate"]
            c["min_ms"] = min(c["min_ms"], left)
            c["n"] += left < 2
        c["xrun"] += v["state"] == "XRUN"
for k, c in cards.items():
    msg = (f"sound card {k}: ALSA buffer {c['buf_ms']:.0f} ms, least left {max(0, c['min_ms']):.0f} ms"
           f"; nearly empty in {c['n']} s; XRUN state in {c['xrun']} s")
    (W if c["n"] or c["xrun"] else I)(msg)
if not cards:
    I("no sound card was playing (or /proc/asound unreadable)")

# the app
http = [s["http_ms"] for s in S if s.get("http_ms") is not None]
pend = [s.get("http_pending_ms", 0) for s in S]
fails = S[-1].get("http_fails", 0)
if http:
    worst = max(max(http), max(pend))
    (W if worst > 500 else I)(f"app response time median {sorted(http)[len(http) // 2]:.0f} ms, max {worst:.0f} ms"
                              + (" (the web page freezes while it is this slow)" if worst > 500 else ""))
if fails:
    W(f"the app did not answer {fails} times (not running, or hung > 10 s)")
if not http and not fails:
    W("could not reach the app on 127.0.0.1")
apps = [s["app"] for s in S if isinstance(s.get("app"), dict)]
if apps and "loop_lag_ms" in apps[-1]:
    a0, a1 = apps[0], apps[-1]
    lag = max((a.get("loop_lag_max_ms", 0) for a in apps), default=0)
    (W if lag > 200 else I)(f"event loop lag max {lag:.0f} ms (app's own measurement)")
    for k, d in (a1.get("cards") or {}).items():
        u = d.get("underruns", 0) - (a0.get("cards") or {}).get(k, {}).get("underruns", 0)
        dr = d.get("dropped", 0) - (a0.get("cards") or {}).get(k, {}).get("dropped", 0)
        (W if u else I)(f"card {k}: {u} underruns, {dr} dropped blocks during the run "
                        f"(since start: {d.get('underruns', 0)} / {d.get('dropped', 0)})")
    eng = a1.get("engine") or {}
    if eng:
        late = eng.get("late", 0) - (a0.get("engine") or {}).get("late", 0)
        (W if late else I)(f"sound engine: {late} blocks late during the run, realtime x{eng.get('realtime', '?')}, "
                           f"restarts {eng.get('restarts', 0)}")
elif apps and "stats" in apps[-1]:
    st0, st1 = apps[0].get("stats", {}), apps[-1].get("stats", {})
    I(f"intercom: underruns {st1.get('underruns', 0) - st0.get('underruns', 0)}, lost {st1.get('lost', 0) - st0.get('lost', 0)} during the run (older app: no /api/health)")

# Wi-Fi
wf = [s["wifi"] for s in S if s.get("wifi")]
if wf:
    down = sum(1 for w in wf if not w.get("sta_connected"))
    sig = [w["signal"] for w in wf if "signal" in w]
    if down:
        W(f"wlan0 (joined Wi-Fi) disconnected in {down} of {len(wf)} checks")
    if sig:
        (W if min(sig) < -75 else I)(f"wlan0 signal {min(sig):.0f} to {max(sig):.0f} dBm"
                                     + (" (weak: below -75 dBm drops out)" if min(sig) < -75 else ""))
    bss = {w.get("bssid") for w in wf if w.get("bssid")}
    if len(bss) > 1:
        W(f"wlan0 roamed between {len(bss)} access points: each roam can move the channel and restart the Pi's AP")
    apc = {w.get("ap_channel") for w in wf}
    stc = {w.get("sta_channel") for w in wf if w.get("sta_channel")}
    if None in apc:
        W(f"the access point (uap0) was down in {sum(1 for w in wf if w.get('ap_channel') is None)} of {len(wf)} checks")
    if len(apc - {None}) > 1:
        W(f"the access point changed channel during the run: {sorted(apc - {None})}")
    I(f"channels: AP {sorted(apc - {None})}, wlan0 {sorted(stc)}; phones on AP {min(w.get('ap_phones', 0) for w in wf)}-"
      f"{max(w.get('ap_phones', 0) for w in wf)}")
    fs = [s for w in wf for s in w.get("ap_signal", [])]
    if fs:
        (W if min(fs) < -75 else I)(f"phones' signal at the Pi {min(fs)} to {max(fs)} dBm")
    tf = wf[-1].get("ap_tx_failed", 0)
    if tf:
        I(f"AP tx failed (to phones, cumulative) {tf}, retries {wf[-1].get('ap_tx_retries', 0)}")
ping = rd("ping.txt")
m = re.search(r"(\d+) packets transmitted, (\d+) received", ping)
if m:
    tx, rx = int(m.group(1)), int(m.group(2))
    rtt = [float(x) for x in re.findall(r"time=([\d.]+)", ping)]
    msg = f"ping to the Wi-Fi's router: {tx - rx}/{tx} lost" + (f", rtt max {max(rtt):.0f} ms" if rtt else "")
    (W if tx - rx > max(1, tx // 50) or (rtt and max(rtt) > 300) else I)(msg)
if re.search(r"get power_save\n+Power save: on", re.sub(r"\$ iw dev (?!" + sta + r" )\S+ get power_save\n+Power save: on", "",
                                                         rd("net-before.txt") + rd("net-after.txt"))):
    W(f"{sta} Wi-Fi power save is ON: a classic cause of brcmfmac drops and latency spikes "
      f"(sudo iw dev {sta} set power_save off; NetworkManager wifi.powersave=2)")

# logs
jn, jk, ja = rd("journal-net.txt"), rd("journal-kernel.txt"), rd("journal-app.txt")
c = lambda pat, txt: len(re.findall(pat, txt, re.I | re.M))
for pat, txt, what in (
        (r"caltreat-ap.*(moving the access point|is down, starting|restarting access point)", jn, "AP channel moves / restarts by caltreat-ap-channel (this boot)"),
        (r"caltreat-ap.*is missing, making it again", jn, "uap0 re-created"),
        (r"(CTRL-EVENT-DISCONNECTED|deauthenticat|disassociat)", jn, "Wi-Fi client disconnects (wpa_supplicant)"),
        (r"device \(" + sta + r"\): state change: activated -> ", jn, f"{sta} left 'activated' (NetworkManager)"),
        (r"device \(uap0\): state change: activated -> ", jn, "uap0 (AP) left 'activated' (NetworkManager)"),
        (r"CTRL-EVENT-BEACON-LOSS|beacon loss", jn, "beacon losses"),
        (r"brcmf_\w+:.*(error|fail|err=|timeout)", jk, "brcmfmac driver errors"),
        (r"Under-?voltage", jk, "kernel undervoltage warnings"),
        (r"usb \S+: (USB disconnect|reset)", jk, "USB disconnects / resets (sound cards?)"),
        (r"oom|Out of memory", jk, "out-of-memory events"),
        (r"stalled", ja, "app: sound card stalled"),
        (r"failed, retrying", ja, "app: sound card player failed"),
        (r"restarting the player", ja, "app: player restarted"),
        (r"underrun", ja, "app: underrun warnings"),
        (r"event loop .*(lag|blocked)", ja, "app: event loop lag warnings"),
        (r"(engine|sidecar).*(restart|exited|died)", ja, "app: sound engine restarts"),
        (r"Traceback", ja, "app: Python tracebacks")):
    k = c(pat, txt)
    if k:
        (W if not what.startswith("AP channel") or k > 1 else I)(f"{what}: {k}")
boots = rd("journal-boots.txt")
m = re.search(r"up (\d+ \w+|[\d:]+)", rd("system.txt"))
if m:
    I(f"uptime: {m.group(1)}")

print("CALTREAT DIAGNOSTICS SUMMARY")
print("(⚠ = looks wrong; details in the other files: samples.jsonl has one line per second)\n")
print("\n".join(warn) if warn else "Nothing obviously wrong in this run.")
print("\nMeasured:")
print("\n".join(info))
PY

{ cat "$OUT/summary.txt"; echo; echo "================================================================ files"; ls -l "$OUT"; } > "$OUT/report.txt"
DEST="$HOME_DIR/$NAME.tar.gz"
tar -C "$WORK" -czf "$DEST" "$NAME"
[[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]] && chown "$SUDO_USER:" "$DEST"
echo
cat "$OUT/summary.txt"
echo
echo "Wrote $DEST  -- please send this file."
echo "From your computer: scp 'caltreat@caltreat.local:$(basename "$DEST")' ."
