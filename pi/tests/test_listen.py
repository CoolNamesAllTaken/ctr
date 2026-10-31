"""listen.py: ports from the environment, the certificate's names (made again with the same key when an address
is new), port fallback, and extra http / https servers beside the app with a certificate switched live."""

import asyncio
import errno
import socket
import ssl
import subprocess

import pytest
from fastapi import FastAPI

from caltreat import listen


def san(cert):
    return listen.cert_names(cert)


def pubkey(cert):
    return subprocess.run(["openssl", "x509", "-noout", "-pubkey", "-in", str(cert)], capture_output=True,
                          text=True).stdout


def test_ports_from_env():
    assert listen.ports_from_env({}) == (8080, [80], [443, 8443])
    assert listen.ports_from_env({"CALTREAT_HTTPS_PORT": "0"}) == (8080, [80], [])  # tests / old setting
    assert listen.ports_from_env({"CALTREAT_HTTPS_PORT": "8453", "CALTREAT_PORT": "8090"}) == (8090, [80], [8453])
    assert listen.ports_from_env({"CALTREAT_HTTP_PORTS": "", "CALTREAT_HTTPS_PORTS": "443, 8443,443"}) == \
        (8080, [], [443, 8443])
    assert listen.ports_from_env({"CALTREAT_PORT": "80", "CALTREAT_HTTP_PORTS": "80 8081"}) == (80, [8081],
                                                                                               [443, 8443])


def test_wanted_names():
    n = listen.wanted_names("caltreat", ["10.26.81.200", "10.42.0.1"], "10.42.0.1")
    assert n[:2] == ["DNS:caltreat.local", "DNS:caltreat"]
    for want in ["DNS:localhost", "IP:10.42.0.1", "IP:192.168.50.1", "IP:127.0.0.1", "IP:10.26.81.200"]:
        assert want in n
    assert len(n) == len(set(n))
    other = listen.wanted_names("trainpi.lan", [], "10.42.0.1")
    assert "DNS:caltreat.local" in other and "DNS:trainpi.local" in other


def test_parse_san():
    text = ("X509v3 Subject Alternative Name: \n    DNS:caltreat.local, DNS:caltreat, IP Address:10.42.0.1, "
            "IP Address:127.0.0.1\n")
    assert listen.parse_san(text) == ["DNS:caltreat.local", "DNS:caltreat", "IP:10.42.0.1", "IP:127.0.0.1"]


def test_merged_names_keeps_some_old_addresses():
    wanted = ["DNS:caltreat.local", "IP:10.0.0.2"]
    old = ["DNS:caltreat.local", "DNS:gone"] + [f"IP:10.1.0.{i}" for i in range(20)]
    m = listen.merged_names(wanted, old)
    assert m[:2] == wanted and "DNS:gone" not in m
    assert m[2:] == [f"IP:10.1.0.{i}" for i in range(12, 20)]


def test_cert_made_once_then_again_with_same_key_for_new_address(tmp_path):
    assert listen.ensure_cert(tmp_path, "caltreat", ["10.26.81.200"], "10.42.0.1") == "made"
    cert, key = tmp_path / "https-cert.pem", tmp_path / "https-key.pem"
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    names = san(cert)
    for want in ["DNS:caltreat.local", "DNS:caltreat", "IP:10.42.0.1", "IP:10.26.81.200", "IP:192.168.50.1"]:
        assert want in names
    first_key, first_pub = key.read_bytes(), pubkey(cert)
    # same addresses (in another order, or fewer): nothing to do
    assert listen.ensure_cert(tmp_path, "caltreat", ["10.26.81.200"], "10.42.0.1") == "ok"
    assert listen.ensure_cert(tmp_path, "caltreat", [], "10.42.0.1") == "ok"
    # joined another Wi-Fi: a new certificate, same key, the old address kept
    assert listen.ensure_cert(tmp_path, "caltreat", ["192.168.1.77"], "10.42.0.1") == "made"
    assert key.read_bytes() == first_key and pubkey(cert) == first_pub
    names = san(cert)
    assert "IP:192.168.1.77" in names and "IP:10.26.81.200" in names


def test_cert_from_old_app_is_upgraded_with_its_key(tmp_path):
    """The app before this change made a cert without caltreat.local's AP address: same key, more names."""
    cert, key = tmp_path / "https-cert.pem", tmp_path / "https-key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "825",
                    "-keyout", str(key), "-out", str(cert), "-subj", "/CN=caltreat.local/O=Caltreat",
                    "-addext", "subjectAltName=DNS:caltreat.local,DNS:caltreat,DNS:localhost,IP:127.0.0.1,"
                                                  "IP:192.168.50.1,IP:10.26.81.200"], check=True, capture_output=True)
    old_pub = pubkey(cert)
    assert listen.ensure_cert(tmp_path, "caltreat", ["10.26.81.200"], "10.42.0.1") == "made"
    assert pubkey(cert) == old_pub and "IP:10.42.0.1" in san(cert)


def test_cert_made_again_when_key_does_not_match_or_expiring(tmp_path):
    assert listen.ensure_cert(tmp_path, "caltreat", [], "10.42.0.1") == "made"
    cert, key = tmp_path / "https-cert.pem", tmp_path / "https-key.pem"
    key.unlink()  # a new key: the old certificate doesn't match it
    assert listen.ensure_cert(tmp_path, "caltreat", [], "10.42.0.1") == "made"
    r = subprocess.run(["openssl", "x509", "-noout", "-pubkey", "-in", str(cert)], capture_output=True, text=True)
    k = subprocess.run(["openssl", "pkey", "-pubout", "-in", str(key)], capture_output=True, text=True)
    assert r.stdout == k.stdout
    # 20 days left (renewed 30 days before the end)
    subprocess.run(["openssl", "req", "-x509", "-key", str(key), "-days", "20", "-out", str(cert),
                    "-subj", "/CN=x", "-addext", "subjectAltName=" + ",".join(
                        listen.wanted_names("caltreat", [], "10.42.0.1"))], check=True, capture_output=True)
    assert listen.ensure_cert(tmp_path, "caltreat", [], "10.42.0.1") == "made"


def test_ap_ip_from_setup_conf(tmp_path, monkeypatch):
    monkeypatch.delenv("CALTREAT_AP_IP", raising=False)
    conf = tmp_path / "network.conf"
    assert listen.ap_ip(conf) == "10.42.0.1"
    conf.write_text("STA_SSID=caltreat\nAP_IP=10.43.0.1\n")
    assert listen.ap_ip(conf) == "10.43.0.1"


def test_bind_error_explains_port_80():
    assert "network.sh" in listen.why(80, PermissionError(errno.EACCES, "Permission denied"))
    assert "in use" in listen.why(8443, OSError(errno.EADDRINUSE, "Address already in use"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def peer_cert_der(port):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw, ctx.wrap_socket(raw) as s:
        return s.getpeercert(binary_form=True)


def test_listeners_serve_http_and_https_retry_a_taken_port_and_switch_cert(tmp_path, monkeypatch):
    app = FastAPI()

    @app.get("/")
    def root():
        return {"ok": True}

    http, https, taken = free_port(), free_port(), free_port()
    blocker = socket.socket()
    blocker.bind(("0.0.0.0", taken))
    blocker.listen()
    monkeypatch.setattr(listen, "current_ips", lambda: ["10.26.81.200"])
    env = {"CALTREAT_PORT": "1", "CALTREAT_HTTP_PORTS": f"{http} {taken}", "CALTREAT_HTTPS_PORTS": str(https)}
    servers = listen.Listeners(app, tmp_path, env)

    async def scenario():
        await servers.start()
        for _ in range(100):
            if all(lst.up for lst in servers.https) and servers.http[0].up:
                break
            await asyncio.sleep(0.05)
        loop = asyncio.get_running_loop()
        out = {}
        out["http"] = await loop.run_in_executor(None, lambda: subprocess.run(
            ["curl", "-fs", f"http://127.0.0.1:{http}/"], capture_output=True, text=True).stdout)
        out["https"] = await loop.run_in_executor(None, lambda: subprocess.run(
            ["curl", "-fsk", f"https://127.0.0.1:{https}/"], capture_output=True, text=True).stdout)
        out["status"] = servers.status()
        out["url_a"] = servers.https_url("caltreat.local")
        out["der1"] = await loop.run_in_executor(None, peer_cert_der, https)
        # the Pi gets a new address: new certificate, the running server hands it out without a restart
        monkeypatch.setattr(listen, "current_ips", lambda: ["10.26.81.200", "192.168.1.77"])
        out["made"] = await servers.check_cert()
        out["der2"] = await loop.run_in_executor(None, peer_cert_der, https)
        blocker.close()  # the taken port frees up: it starts there within the retry delay
        for _ in range(80):
            if servers.http[1].up:
                break
            await asyncio.sleep(0.05)
        out["taken_up"] = servers.http[1].up
        servers.stop()
        await asyncio.sleep(0.3)
        return out

    out = asyncio.run(scenario())
    assert out["http"] == '{"ok":true}' and out["https"] == '{"ok":true}'
    assert out["status"][http] == "" and out["status"][https] == "" and "in use" in out["status"][taken]
    assert out["url_a"] == f"https://caltreat.local:{https}/"
    assert out["made"] is True and out["der1"] != out["der2"]
    assert "IP:192.168.1.77" in san(tmp_path / "https-cert.pem")
    assert out["taken_up"]


def test_https_url_prefers_443():
    servers = listen.Listeners(FastAPI(), "/nonexistent", {"CALTREAT_HTTPS_PORTS": "443 8443"})

    class Up:
        started = True
    servers.https[1].server = Up()
    assert servers.https_url("caltreat.local") == "https://caltreat.local:8443/"
    servers.https[0].server = Up()
    assert servers.https_url("10.42.0.1") == "https://10.42.0.1/"
    assert servers.https_url("fe80::1") == "https://[fe80::1]/"
    servers.https[0].server = servers.https[1].server = None
    assert servers.https_url("caltreat.local") == ""
