"""network.py / network_ui.py: nmcli and iw output parsed into the settings page's Network rows."""

from caltreat import network, network_ui

DEVICES = """eth0:ethernet:connected:boards
wlan0:wifi:connected:preconfigured
uap0:wifi:connected:caltreat-ap
lo:loopback:connected (externally):lo
p2p-dev-wlan0:wifi-p2p:disconnected:
"""
WIFI = """no:40:1:Neighbors\\: 5G
yes:72:11:caltreat
no:30:6:Caltreat Cab
"""
IW_INFO = """Interface uap0
\tifindex 4
\twdev 0x2
\taddr dc:a6:32:00:00:02
\tssid Caltreat Cab
\ttype AP
\twiphy 0
\tchannel 11 (2462 MHz), width: 20 MHz, center1: 2462 MHz
\ttxpower 31.00 dBm
"""
STATIONS = "Station 11:22:33:44:55:66 (on uap0)\n\tinactive time:\t10 ms\nStation 22:33:44:55:66:77 (on uap0)\n"


def fake_run(table):
    def run(*cmd):
        return table.get(cmd)
    return run


def test_terse():
    assert network.terse("no:40:1:Neighbors\\: 5G") == ["no", "40", "1", "Neighbors: 5G"]
    assert network.terse("a\\\\b:") == ["a\\b", ""]


def test_parsers():
    d = network.parse_devices(DEVICES)
    assert d["uap0"] == {"type": "wifi", "state": "connected", "connection": "caltreat-ap"}
    assert network.parse_wifi_list(WIFI) == {"ssid": "caltreat", "signal": 72, "chan": 11}
    assert network.parse_wifi_list("no:1:1:x\n") is None
    assert network.parse_ip("10.42.0.1/24 | 10.42.0.2/24\n") == "10.42.0.1"
    assert network.parse_ip("") == ""
    assert network.parse_iw_info(IW_INFO) == 11
    assert network.parse_stations(STATIONS) == 2


def test_read_conf(tmp_path):
    p = tmp_path / "network.conf"
    p.write_text("# x\nSTA_SSID=caltreat\nAP_SSID=Caltreat\\ Cab\nAP_IFACE=wlan1\nAP_IP=10.42.0.1\n")
    c = network.read_conf(p)
    assert c["AP_SSID"] == "Caltreat Cab" and c["AP_IFACE"] == "wlan1" and c["BOARD_IFACE"] == "eth0"
    p.write_text("AP_SSID=$'Caf\\xc3\\xa9'\n")
    assert network.read_conf(p)["AP_SSID"].encode("latin-1").decode() == "Café"
    assert network.read_conf(tmp_path / "missing")["STA_SSID"] == "caltreat"


def test_status_and_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(network, "CONF", tmp_path / "network.conf")
    (tmp_path / "network.conf").write_text("AP_SSID=Caltreat\\ Cab\n")
    table = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"): DEVICES,
        ("nmcli", "-g", "IP4.ADDRESS", "device", "show", "wlan0"): "10.26.81.200/24\n",
        ("nmcli", "-g", "IP4.ADDRESS", "device", "show", "uap0"): "10.42.0.1/24\n",
        ("nmcli", "-g", "IP4.ADDRESS", "device", "show", "eth0"): "192.168.50.1/24\n",
        ("nmcli", "-t", "-f", "ACTIVE,SIGNAL,CHAN,SSID", "device", "wifi", "list", "ifname", "wlan0", "--rescan",
         "no"): WIFI,
        ("iw", "dev", "uap0", "info"): IW_INFO,
        ("iw", "dev", "uap0", "station", "dump"): STATIONS,
    }
    s = network.status(fake_run(table))
    assert s["sta"]["ssid"] == "caltreat" and s["sta"]["ip"] == "10.26.81.200" and s["sta"]["signal"] == 72
    assert s["ap"]["clients"] == 2 and s["ap"]["chan"] == 11 and s["ap"]["ip"] == "10.42.0.1"
    rows = network_ui.lines(s, {8080: "", 80: "", 443: "", 8443: ""})
    assert rows[0] == ("Joins", "caltreat · 10.26.81.200 · 72% · ch 11", True, rows[0][3])
    assert rows[1][:3] == ("Serves", "Caltreat Cab · 10.42.0.1 · 2 phones · ch 11", True)
    assert rows[2][:3] == ("Boards", "192.168.50.1 (connected)", True)
    assert rows[3][:3] == ("Ports", "80 · 443 · 8080 · 8443", True)

    # out of range of "caltreat", the access point never set up, port 80 not allowed yet
    table[("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")] = \
        "eth0:ethernet:connected:boards\nwlan0:wifi:disconnected:\n"
    (tmp_path / "network.conf").unlink()
    s = network.status(fake_run(table))
    rows = network_ui.lines(s, {8080: "", 80: "port 80 needs the one-time network setup"})
    assert rows[0][1].startswith("looking for “caltreat”") and not rows[0][2]
    assert rows[1][1] == "not set up: sudo ~/caltreat/setup/network.sh" and not rows[1][2]
    assert rows[3][:3] == ("Ports", "not on 80", False) and "network setup" in rows[3][3]


def test_no_networkmanager():
    s = network.status(lambda *cmd: None)
    assert s["nm"] is False
    assert network_ui.lines(s, {})[0][2] is False
    assert network_ui.lines(None, {})[0][1] == "…"


def test_join_switch_state_and_rows(monkeypatch, tmp_path):
    """The Join switch: disabled without the setup's rule, off = access point only (row + button)."""
    monkeypatch.setattr(network, "CONF", tmp_path / "network.conf")
    monkeypatch.setattr(network, "STA_OFF", tmp_path / "sta-off")
    monkeypatch.setattr(network, "STA_UNIT", tmp_path / "caltreat-sta@.service")
    (tmp_path / "network.conf").write_text("AP_SSID=Caltreat\\ Cab\nSTA_TOGGLE=0\n")
    table = {("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"):
             "wlan0:wifi:connected:caltreat-sta\nuap0:wifi:connected:caltreat-ap\n"}
    s = network.status(fake_run(table))
    assert s["sta"]["toggle"] is False and s["sta"]["off"] is False
    text, cls, tip, enabled = network_ui.switch_state(s)
    assert text == "● on" and not enabled and "sudo ~/caltreat/setup/network.sh" in tip

    (tmp_path / "network.conf").write_text("AP_SSID=Caltreat\\ Cab\nSTA_TOGGLE=1\n")
    s = network.status(fake_run(table))
    assert s["sta"]["toggle"] is False  # the rule's unit isn't there
    (tmp_path / "caltreat-sta@.service").write_text("[Service]\n")
    s = network.status(fake_run(table))
    text, cls, tip, enabled = network_ui.switch_state(s)
    assert s["sta"]["toggle"] and enabled and text == "● on" and cls == "sbtn on"
    assert "access point only" in tip and "Caltreat Cab" in tip and "ethernet" in tip
    assert network_ui.switch_state(s, busy=True)[3] is False

    (tmp_path / "sta-off").write_text("off\n")
    table[("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")] = \
        "wlan0:wifi:disconnected:\nuap0:wifi:connected:caltreat-ap\n"
    s = network.status(fake_run(table))
    rows = network_ui.lines(s, {8080: ""})
    assert rows[0][:3] == ("Joins", "off: access point only (not joining “caltreat”)", True)
    text, cls, tip, enabled = network_ui.switch_state(s)
    assert text == "○ off" and enabled and tip.startswith("Turn on")


def test_set_sta_runs_only_the_two_units():
    calls = []

    class R:
        def __init__(self, code, err=""):
            self.returncode, self.stderr, self.stdout = code, err, ""

    def run(cmd, **kw):
        calls.append(cmd)
        return R(0)
    assert network.set_sta(False, run) == (True, "access point only")
    assert network.set_sta(True, run) == (True, "joining")
    assert calls == [["systemctl", "--no-ask-password", "start", "caltreat-sta@off.service"],
                     ["systemctl", "--no-ask-password", "start", "caltreat-sta@on.service"]]
    ok, msg = network.set_sta(False, lambda cmd, **kw: R(1, "Failed to start caltreat-sta@off.service: Access denied"))
    assert not ok and "Access denied" in msg and "setup/network.sh" in msg
