"""pi/setup/network.sh and the helpers it installs, run against stand-ins for nmcli, iw, systemctl, sysctl, ip,
hostname and logger (no Wi-Fi hardware here). Files go into a temp folder (CALTREAT_SETUP_ROOT); every command the
scripts run is logged and checked."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parent.parent / "setup"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("flock") is None,
                                reason="needs bash and flock")

STUB = r"""#!/bin/bash
# Stand-in for nmcli / iw / systemctl / ...: logs the call, answers from files in $STUB_DIR.
me="$(basename "$0")"
echo "$me $*" >> "$STUB_DIR/log"
d="$STUB_DIR"
case "$me $*" in
  "hostname"*) echo caltreat ;;
  "nmcli -t -f NAME,TYPE connection show") cat "$d/conns" 2>/dev/null ;;
  "nmcli -g 802-11-wireless.ssid connection show "*) n="${*: -1}"; cat "$d/ssid-$n" 2>/dev/null ;;
  "nmcli -g 802-11-wireless.channel connection show "*) cat "$d/apchan" 2>/dev/null ;;
  "nmcli -t -f NAME connection show") cut -d: -f1 "$d/conns" 2>/dev/null ;;
  "nmcli -t -f NAME connection show --active") cat "$d/active" 2>/dev/null ;;
  "nmcli -t -f CHAN,SIGNAL,SSID device wifi list"*) cat "$d/scan" 2>/dev/null ;;
  "nmcli connection modify caltreat-ap"*) echo "${*: -1}" > "$d/apchan" ;;
  "iw list") printf 'valid interface combinations:\n\t * #{ managed } <= 1, #{ AP } <= 1, #{ P2P-client } <= 1,\n\t   total <= 3, #channels <= 1\n' ;;
  "iw reg get") echo "global"; echo "country US: DFS-FCC" ;;
  "iw dev wlan0 link") cat "$d/link" 2>/dev/null || echo "Not connected." ;;
  "iw dev wlan0 info") echo "Interface wlan0"; echo "	channel $(cat "$d/stachan" 2>/dev/null) (2462 MHz)" ;;
  "systemctl is-active"*) exit 0 ;;
  "systemctl is-enabled"*) exit 1 ;;
  "sysctl -n"*) echo 1024 ;;
esac
exit 0
"""


class Pi:
    def __init__(self, tmp):
        self.tmp = tmp
        self.root = tmp / "root"
        self.stub = tmp / "stub"
        self.bin = tmp / "bin"
        self.sys = tmp / "sys"
        for p in (self.root, self.stub, self.bin, self.sys / "class/net/wlan0/phy80211"):
            p.mkdir(parents=True)
        (self.sys / "class/net/wlan0/phy80211/name").write_text("phy0\n")
        (self.sys / "class/net/wlan0/address").write_text("dc:a6:32:12:34:56\n")
        stub = self.bin / "stub"
        stub.write_text(STUB)
        stub.chmod(0o755)
        for name in ("nmcli", "iw", "systemctl", "sysctl", "ip", "hostname", "logger", "raspi-config"):
            (self.bin / name).symlink_to(stub)
        self.env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}", "STUB_DIR": str(self.stub), "HOME": str(tmp),
            "CALTREAT_SETUP_ROOT": str(self.root), "CALTREAT_SYS": str(self.sys),
            "CALTREAT_NETWORK_CONF": str(self.root / "etc/caltreat/network.conf"),
            "CALTREAT_SBIN": str(self.root / "usr/local/sbin"), "CALTREAT_LOCK": str(tmp / "lock"),
        }

    def run(self, *args, script=SETUP / "network.sh", **env):
        (self.stub / "log").write_text("")
        r = subprocess.run(["bash", str(script), *args], env={**self.env, **env}, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=60)
        r.log = (self.stub / "log").read_text()
        return r

    def file(self, rel):
        return self.root / rel

    def set(self, name, text):
        (self.stub / name).write_text(text)


@pytest.fixture
def pi(tmp_path):
    return Pi(tmp_path)


AP = "etc/NetworkManager/system-connections/caltreat-ap.nmconnection"
STA = "etc/NetworkManager/system-connections/caltreat-sta.nmconnection"
SECRET_AP, SECRET_STA = "train-cab-2026", "home wifi \\pass"


def mode(p):
    return oct(stat.S_IMODE(p.stat().st_mode))


def test_shellcheck():
    sc = shutil.which("shellcheck") or "/workspace/tools/age-shellcheck/bin/shellcheck"
    if not Path(sc).exists():
        pytest.skip("no shellcheck")
    files = [SETUP / "network.sh", *[SETUP / "files" / f for f in ("caltreat-uap0", "caltreat-ap-channel",
                                                                   "90-caltreat-ap")]]
    r = subprocess.run([sc, "-x", *map(str, files)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_dry_run_changes_nothing_and_hides_passwords(pi):
    r = pi.run("--dry-run", AP_PASS=SECRET_AP, STA_PASS=SECRET_STA)
    assert r.returncode == 0, r.stderr
    assert "would write" in r.stdout and AP in r.stdout
    assert not any(pi.root.rglob("*")), "dry run wrote files"
    assert SECRET_AP not in r.stdout + r.stderr and SECRET_STA not in r.stdout + r.stderr
    assert "nmcli connection load" not in r.log and "systemctl enable" not in r.log


def test_install_then_again_is_idempotent(pi):
    r = pi.run(AP_PASS=SECRET_AP, STA_PASS=SECRET_STA, WIFI_COUNTRY="US", CALTREAT_SETUP_APPLY="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert SECRET_AP not in r.stdout + r.stderr and SECRET_STA not in r.stdout + r.stderr
    ap = pi.file(AP).read_text()
    assert mode(pi.file(AP)) == "0o600" and mode(pi.file(STA)) == "0o600"
    for line in ["interface-name=uap0", "mode=ap", "ssid=Caltreat Cab", "channel=6", "band=bg",
                 "key-mgmt=wpa-psk", f"psk={SECRET_AP}", "method=shared", "address1=10.42.0.1/24", "powersave=2"]:
        assert line + "\n" in ap
    sta = pi.file(STA).read_text()
    for line in ["interface-name=wlan0", "ssid=caltreat", "psk=home wifi \\\\pass", "autoconnect-retries=0"]:
        assert line + "\n" in sta
    conf = pi.file("etc/caltreat/network.conf").read_text()
    assert "AP_SSID=Caltreat\\ Cab" in conf and "AP_IP=10.42.0.1" in conf and SECRET_AP not in conf and "home wifi" not in conf
    assert "net.ipv4.ip_unprivileged_port_start=80" in pi.file("etc/sysctl.d/90-caltreat-ports.conf").read_text()
    hosts = pi.file("etc/caltreat/hosts").read_text()
    assert "10.42.0.1 caltreat.local caltreat" in hosts and "192.168.50.1 caltreat.local" in hosts
    dns = pi.file("etc/NetworkManager/dnsmasq-shared.d/caltreat.conf").read_text()
    assert "addn-hosts=/etc/caltreat/hosts" in dns and "localise-queries" in dns and "address=/#/" not in dns
    unit = pi.file("etc/systemd/system/caltreat-uap0.service").read_text()
    assert "BindsTo=sys-subsystem-net-devices-wlan0.device" in unit and "@" not in unit
    for f in ("usr/local/sbin/caltreat-uap0", "usr/local/sbin/caltreat-ap-channel",
              "etc/NetworkManager/dispatcher.d/90-caltreat-ap"):
        assert mode(pi.file(f)) == "0o755"
    log = r.log
    assert "raspi-config nonint do_wifi_country US" not in log  # already US
    assert "systemctl enable caltreat-uap0.service" in log
    assert "systemctl start caltreat-uap0.service" in log
    assert "systemctl enable --now caltreat-ap-channel.timer" in log
    assert f"nmcli connection load {pi.file(AP)}" in log and f"nmcli connection load {pi.file(STA)}" in log
    # the channel helper ran: it made uap0 (with its own MAC) and started the access point
    assert "iw phy phy0 interface add uap0 type __ap" in log
    assert "ip link set dev uap0 address de:a6:32:12:34:56" in log
    assert "nmcli connection up caltreat-ap" in log
    assert "nmcli connection modify preconfigured" not in log

    again = pi.run(CALTREAT_SETUP_APPLY="1")  # no passwords given: the saved ones are kept
    assert again.returncode == 0, again.stdout + again.stderr
    assert "writing" not in again.stdout and "unchanged: " + str(pi.file(AP)) in again.stdout
    assert pi.file(AP).read_text() == ap and pi.file(STA).read_text() == sta
    assert "connection load" not in again.log and "daemon-reload" not in again.log


def test_existing_wifi_connection_is_adjusted_not_replaced(pi):
    pi.set("conns", "lo:loopback\npreconfigured:802-11-wireless\nboards:802-3-ethernet\n")
    pi.set("ssid-preconfigured", "caltreat\n")
    r = pi.run(AP_PASS=SECRET_AP, STA_PASS=SECRET_STA, CALTREAT_SETUP_APPLY="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not pi.file(STA).exists()
    assert "nmcli connection modify preconfigured 802-11-wireless.powersave 2" in r.log
    assert "connection.autoconnect-retries 0" in r.log
    assert f"wifi-sec.psk {SECRET_STA}" in r.log  # given to nmcli ...
    assert SECRET_STA not in r.stdout and "<hidden>" in r.stdout  # ... but not printed
    assert "nmcli connection up preconfigured" not in r.log and "nmcli device" not in r.log  # SSH over wlan0 stays


def test_bad_or_missing_passwords(pi):
    r = pi.run(AP_PASS="short", STA_PASS=SECRET_STA)
    assert r.returncode == 1 and "8-63" in r.stderr
    r = pi.run(STA_PASS=SECRET_STA)  # no terminal to ask on, nothing saved
    assert r.returncode == 1 and "AP_PASS" in r.stderr
    r = pi.run(AP_PASS=SECRET_AP)
    assert r.returncode == 1 and "STA_PASS" in r.stderr
    assert not pi.file(AP).exists()


def test_usb_adapter_for_the_access_point(pi):
    r = pi.run(AP_PASS=SECRET_AP, STA_PASS=SECRET_STA, AP_IFACE="wlan1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "interface-name=wlan1" in pi.file(AP).read_text()
    assert not pi.file("etc/systemd/system/caltreat-uap0.service").exists()
    assert "SHARED_RADIO=0" in pi.file("etc/caltreat/network.conf").read_text()


def test_undo(pi):
    assert pi.run(AP_PASS=SECRET_AP, STA_PASS=SECRET_STA).returncode == 0
    pi.set("conns", "caltreat-ap:802-11-wireless\ncaltreat-sta:802-11-wireless\n")
    r = pi.run("--undo", CALTREAT_SETUP_APPLY="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "nmcli connection delete caltreat-ap" in r.log
    assert "systemctl disable --now caltreat-ap-channel.timer" in r.log
    assert "sysctl -w net.ipv4.ip_unprivileged_port_start=1024" in r.log
    left = sorted(str(p.relative_to(pi.root)) for p in pi.root.rglob("*") if p.is_file())
    assert left == [STA]  # the joined Wi-Fi's connection stays


# ---- the channel helper (installed as /usr/local/sbin/caltreat-ap-channel)
@pytest.fixture
def helper(pi):
    assert pi.run(AP_PASS=SECRET_AP, STA_PASS=SECRET_STA).returncode == 0
    (pi.sys / "class/net/uap0").mkdir()
    pi.set("apchan", "6\n")
    pi.set("active", "caltreat-ap\npreconfigured\n")
    return pi


def channel(pi):
    return pi.run(script=pi.file("usr/local/sbin/caltreat-ap-channel"))


def test_ap_follows_the_joined_wifi(helper):
    helper.set("link", "Connected to 11:22:33:44:55:66 (on wlan0)\n")
    helper.set("stachan", "11")
    r = channel(helper)
    assert r.returncode == 0, r.stderr
    assert "nmcli connection modify caltreat-ap 802-11-wireless.band bg 802-11-wireless.channel 11" in r.log
    assert "nmcli connection up caltreat-ap" in r.log
    r = channel(helper)  # now on 11: nothing to do
    assert "modify" not in r.log and "connection up" not in r.log


def test_ap_moves_to_where_the_wifi_is_seen_when_not_joined(helper):
    helper.set("scan", "1:30:other\n36:70:caltreat\n6:40:caltreat\n149:50:Neigh\\:bor\n")
    r = channel(helper)
    assert "802-11-wireless.band a 802-11-wireless.channel 36" in r.log


def test_ap_stays_off_radar_channels(helper):
    helper.set("scan", "100:70:caltreat\n")
    r = channel(helper)
    assert "modify" not in r.log and "radar" in r.stdout


def test_ap_stays_when_wifi_absent_and_restarts_when_down(helper):
    helper.set("active", "preconfigured\n")
    r = channel(helper)
    assert "modify" not in r.log and "nmcli connection up caltreat-ap" in r.log


def test_restart_flag_restarts_a_running_ap_once(helper):
    r = helper.run("--restart", script=helper.file("usr/local/sbin/caltreat-ap-channel"))
    assert r.returncode == 0, r.stderr
    assert "modify" not in r.log and r.log.count("nmcli connection up caltreat-ap") == 1
    # a channel move already restarts it: not twice
    helper.set("link", "Connected to 11:22:33:44:55:66 (on wlan0)\n")
    helper.set("stachan", "11")
    r = helper.run("--restart", script=helper.file("usr/local/sbin/caltreat-ap-channel"))
    assert "802-11-wireless.channel 11" in r.log and r.log.count("nmcli connection up caltreat-ap") == 1


def test_new_ap_password_restarts_the_running_ap(helper):
    # The access point is up on the right channel; only its password changes. Loading the file alone would leave
    # it running with the old password.
    r = helper.run(AP_PASS="a-new-cab-pass", CALTREAT_SETUP_APPLY="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "psk=a-new-cab-pass\n" in helper.file(AP).read_text()
    assert f"nmcli connection load {helper.file(AP)}" in r.log
    assert r.log.count("nmcli connection up caltreat-ap") == 1


def test_uap0_mac_when_wlan0_is_already_local(helper):
    (helper.sys / "class/net/uap0").rmdir()
    (helper.sys / "class/net/wlan0/address").write_text("de:a6:32:12:34:56\n")
    r = helper.run("up", script=helper.file("usr/local/sbin/caltreat-uap0"))
    assert "ip link set dev uap0 address de:a6:32:12:34:57" in r.log


def test_dispatcher(helper):
    hook = helper.file("etc/NetworkManager/dispatcher.d/90-caltreat-ap")
    for args, expect in ((("wlan0", "up"), True), (("wlan0", "down"), True), (("eth0", "up"), False),
                         (("wlan0", "dhcp4-change"), False)):
        r = subprocess.run(["sh", str(hook), *args], env=helper.env, capture_output=True, text=True)
        assert r.returncode == 0
        log = (helper.stub / "log").read_text()
        assert ("systemctl start --no-block caltreat-ap-channel.service" in log) == expect, args
        (helper.stub / "log").write_text("")
