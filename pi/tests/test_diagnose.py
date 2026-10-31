"""pi/tools/diagnose.sh: runs (briefly) against stand-ins for the Pi's tools and the app, and its summary finds
what they report: undervoltage, Wi-Fi power save, AP restarts, a slow app, underruns."""

import json
import os
import shutil
import stat
import subprocess
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "diagnose.sh"

STUBS = {
    "vcgencmd": r"""
case "$1" in
  get_throttled) echo throttled=0x50005 ;;
  measure_temp) echo "temp=61.2'C" ;;
  measure_clock) echo "frequency(48)=1500398464" ;;
  *) echo "$*: ok" ;;
esac""",
    "iw": r"""
case "$*" in
  "dev wlan0 link") printf 'Connected to aa:bb:cc:dd:ee:01 (on wlan0)\n\tSSID: caltreat\n\tfreq: 2437\n\tsignal: -79 dBm\n\ttx bitrate: 65.0 MBit/s\n' ;;
  "dev wlan0 info") printf 'Interface wlan0\n\tchannel 6 (2437 MHz), width: 20 MHz\n' ;;
  "dev uap0 info") printf 'Interface uap0\n\tchannel 6 (2437 MHz), width: 20 MHz\n' ;;
  "dev uap0 station dump") printf 'Station 11:22:33:44:55:66 (on uap0)\n\tinactive time:\t120 ms\n\ttx retries:\t5\n\ttx failed:\t2\n\tsignal:  \t-58 dBm\n' ;;
  "dev wlan0 get power_save") echo "Power save: on" ;;
  "dev uap0 get power_save") echo "Power save: off" ;;
  dev) printf 'phy#0\n\tInterface uap0\n\tInterface wlan0\n' ;;
esac""",
    "ip": r"""
case "$*" in
  "route show default"*) echo "default via 192.168.8.1 dev wlan0 proto dhcp" ;;
  *) echo "ip $*" ;;
esac""",
    "ping": r"""
for i in 1 2 3 4 5 6 7 8; do echo "[1.0] 64 bytes from 192.168.8.1: icmp_seq=$i ttl=64 time=$((i * 60)).0 ms"; done
echo "10 packets transmitted, 8 received, 20% packet loss, time 9000ms"
sleep 30""",
    "journalctl": r"""
case "$*" in
  *caltreat-ap*)
    for i in 1 2 3 4; do echo "2026-10-31T20:0$i:00 caltreat caltreat-ap[9]: moving the access point from channel 1 to 6 (the channel of caltreat)"; done
    echo "2026-10-31T20:05:00 caltreat wpa_supplicant[7]: wlan0: CTRL-EVENT-DISCONNECTED bssid=aa:bb:cc:dd:ee:01 reason=4" ;;
  "-k -b -1"*) echo "-- No entries --" ;;
  "-k "*) echo "2026-10-31T20:00:00 caltreat kernel: brcmfmac: brcmf_cfg80211_get_tx_power: error (-110)"
            echo "2026-10-31T20:00:01 caltreat kernel: hwmon hwmon1: Undervoltage detected!" ;;
  *caltreat.service*) echo "2026-10-31T20:00:02 caltreat python[5]: caltreat: sound card usb:1.2 (Device) stalled" ;;
  *) echo "-- No entries --" ;;
esac""",
    "nmcli": 'echo "nmcli $*"',
    "systemctl": 'echo "systemctl $*"',
}


class Health(BaseHTTPRequestHandler):
    n = 0

    def do_GET(self):
        if self.path != "/api/health":
            self.send_error(404)
            return
        Health.n += 1
        if Health.n == 3:
            time.sleep(1.5)  # one slow answer: a busy event loop
        body = json.dumps({"loop_lag_ms": 3, "loop_lag_max_ms": 450 if Health.n > 2 else 10,
                           "cards": {"usb:1.2": {"underruns": 10 + Health.n, "dropped": 1}},
                           "engine": {"late": 0, "realtime": 4.2, "restarts": 0}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def stubs(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in STUBS.items():
        p = bin_ / name
        p.write_text("#!/bin/bash\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return bin_


def test_diagnose_summary(tmp_path, stubs):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), PATH=f"{stubs}:{os.environ['PATH']}", CALTREAT_PORT=str(srv.server_port),
               STA_IFACE="wlan0", AP_IFACE="uap0")
    try:
        r = subprocess.run(["bash", str(SCRIPT), "10"], env=env, capture_output=True, text=True, timeout=120)
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    tars = list(home.glob("caltreat-diag-*.tar.gz"))
    assert len(tars) == 1
    with tarfile.open(tars[0]) as t:
        names = t.getnames()
        report = t.extractfile(next(n for n in names if n.endswith("/report.txt"))).read().decode()
        samples = t.extractfile(next(n for n in names if n.endswith("/samples.jsonl"))).read().decode()
    assert len(samples.splitlines()) >= 9
    head = report.split("Measured:")[0]
    assert "UNDERVOLTAGE" in head and "0x50005" in head
    assert "wlan0 Wi-Fi power save is ON" in head
    assert "AP channel moves / restarts by caltreat-ap-channel (this boot): 4" in head
    assert "Wi-Fi client disconnects (wpa_supplicant): 1" in head
    assert "brcmfmac driver errors: 1" in head
    assert "app: sound card stalled: 1" in head
    assert "event loop lag max 450 ms" in head
    assert "card usb:1.2:" in head and "underruns" in head
    assert "2/10 lost" in head
    assert "weak" in head  # -79 dBm
    assert "app response time" in head  # the 1.5 s answer
    assert r.stdout.count("CALTREAT DIAGNOSTICS SUMMARY") == 1


def test_diagnose_usage():
    r = subprocess.run(["bash", str(SCRIPT), "abc"], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
