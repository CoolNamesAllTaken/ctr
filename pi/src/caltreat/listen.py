"""Where the app listens, and its HTTPS certificate.

NiceGUI's own server serves http on CALTREAT_PORT (8080) and must start. Beside it, in the same event loop and for
the same app, this module runs:
  - http on CALTREAT_HTTP_PORTS (default 80), so http://caltreat.local/ works without a port;
  - https on CALTREAT_HTTPS_PORTS (default 443 and 8443; the phone's microphone needs a secure page). The old
    CALTREAT_HTTPS_PORT, if set, picks one https port instead (0: no https).
Each of those is best-effort: if it can't listen (port 80/443 need `sudo ~/caltreat/setup/network.sh` once, which
lets ordinary users listen on ports from 80 up; or the port is taken) it tries again, 2 s later, then 4, 8, ... up
to every 60 s, quietly, while the others keep working.

The certificate is self-signed, made on first start in the config folder (https-cert.pem, https-key.pem). Its
names (SANs) are caltreat.local, caltreat, the Pi's own Wi-Fi address (10.42.0.1), the boards' network address and
every address the Pi has. When the Pi gets a new address (it joins another Wi-Fi), the certificate is made again
with the same key and the new name added (old addresses are kept), and the running https servers switch to it
without a restart. Phones remember an accepted certificate, not the key, so they ask once more after that; it's
rare, as phones on the Pi's own Wi-Fi use caltreat.local / 10.42.0.1, which never change.

    servers = Listeners(app, cert_folder)
    app.on_startup(servers.start); app.on_shutdown(servers.stop)
    servers.https_url("caltreat.local")  # "https://caltreat.local/" (443 up), ":8443/" (only 8443 up), or ""
"""

import asyncio
import contextlib
import errno
import ipaddress
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import uvicorn

NETWORK_CONF = Path(os.environ.get("CALTREAT_NETWORK_CONF", "/etc/caltreat/network.conf"))
BOARD_IP = "192.168.50.1"  # the Pi on the boards' wired network (eth0)
DEFAULT_AP_IP = "10.42.0.1"
KEEP_OLD_IPS = 8  # addresses from earlier certificates kept in a new one
CERT_DAYS = 825  # the most Apple accepts for a server certificate
CHECK_EVERY = 60.0  # s, look for new addresses


def _ports(text):
    out = []
    for p in re.split(r"[,\s]+", text.strip()):
        if p and int(p) > 0 and int(p) not in out:
            out.append(int(p))
    return out


def ports_from_env(env=None):
    """(main http port, extra http ports, https ports) from the environment."""
    env = os.environ if env is None else env
    main = int(env.get("CALTREAT_PORT", "8080"))
    http = [p for p in _ports(env.get("CALTREAT_HTTP_PORTS", "80")) if p != main]
    if env.get("CALTREAT_HTTPS_PORT") is not None:
        https = _ports(env["CALTREAT_HTTPS_PORT"])
    else:
        https = _ports(env.get("CALTREAT_HTTPS_PORTS", "443 8443"))
    https = [p for p in https if p != main and p not in http]
    return main, http, https


def ap_ip(conf=NETWORK_CONF):
    """The Pi's address on its own Wi-Fi: CALTREAT_AP_IP, else the setup script's network.conf, else 10.42.0.1."""
    if os.environ.get("CALTREAT_AP_IP"):
        return os.environ["CALTREAT_AP_IP"]
    try:
        m = re.search(r"^AP_IP=([0-9.]+)\s*$", Path(conf).read_text(), re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    return DEFAULT_AP_IP


def current_ips():
    """The Pi's IPv4 addresses now (not loopback or link-local)."""
    try:
        words = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for w in words:
        try:
            ip = ipaddress.ip_address(w)
        except ValueError:
            continue
        if ip.version == 4 and not ip.is_loopback and not ip.is_link_local and w not in out:
            out.append(w)
    return out


def wanted_names(host, ips, ap=DEFAULT_AP_IP):
    """The certificate's names (OpenSSL subjectAltName form), most important first."""
    host = (host or "caltreat").split(".")[0] or "caltreat"
    names = ["DNS:caltreat.local", "DNS:caltreat", f"DNS:{host}.local", f"DNS:{host}", "DNS:localhost",
             f"IP:{ap}", f"IP:{BOARD_IP}", "IP:127.0.0.1", *[f"IP:{ip}" for ip in ips]]
    return list(dict.fromkeys(names))


def parse_san(text):
    """Names from `openssl x509 -ext subjectAltName` output, as DNS:x / IP:y."""
    out = []
    for part in re.split(r"[,\n]", text):
        part = part.strip()
        if part.startswith("DNS:"):
            out.append(part)
        elif part.startswith("IP Address:"):
            out.append("IP:" + part[len("IP Address:"):].strip())
    return out


def merged_names(wanted, old):
    """New certificate names: the wanted ones, plus up to KEEP_OLD_IPS addresses from the old certificate (so a
    phone that opened the Pi by an old address keeps matching it)."""
    old_ips = [n for n in (old or []) if n.startswith("IP:") and n not in wanted]
    return wanted + old_ips[-KEEP_OLD_IPS:]


def _openssl(*args):
    return subprocess.run(["openssl", *args], capture_output=True, text=True, timeout=60)


def cert_names(cert):
    r = _openssl("x509", "-noout", "-ext", "subjectAltName", "-in", str(cert))
    return parse_san(r.stdout) if r.returncode == 0 else None


def _pubkey(args):
    r = _openssl(*args)
    return r.stdout.strip() if r.returncode == 0 else None


def ensure_cert(folder, host=None, ips=None, ap=None):
    """Make sure folder/https-cert.pem is valid for 30 more days, matches https-key.pem and names everything
    wanted_names() lists. Returns "ok" (nothing to do), "made" (new certificate) or "failed"."""
    folder = Path(folder)
    cert, key = folder / "https-cert.pem", folder / "https-key.pem"
    host = host if host is not None else socket.gethostname()
    ips = current_ips() if ips is None else ips
    wanted = wanted_names(host, ips, ap or ap_ip())
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not key.exists():
            tmp = key.with_suffix(".tmp")
            r = _openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(tmp))
            if r.returncode:
                print(f"caltreat: could not make the HTTPS key: {r.stderr.strip()}", file=sys.stderr)
                return "failed"
            tmp.chmod(0o600)
            tmp.replace(key)
        old = None
        if cert.exists():
            fresh = _openssl("x509", "-checkend", str(30 * 86400), "-noout", "-in", str(cert)).returncode == 0
            same_key = _pubkey(["x509", "-noout", "-pubkey", "-in", str(cert)]) == _pubkey(
                ["pkey", "-pubout", "-in", str(key)])
            old = cert_names(cert)
            if fresh and same_key and old is not None and set(wanted) <= set(old):
                return "ok"
        names = merged_names(wanted, old)
        tmp = cert.with_suffix(".tmp")
        r = _openssl("req", "-x509", "-key", str(key), "-sha256", "-days", str(CERT_DAYS), "-out", str(tmp),
                     "-subj", "/CN=caltreat.local/O=Caltreat", "-addext", "subjectAltName=" + ",".join(names),
                     "-addext", "extendedKeyUsage=serverAuth")
        if r.returncode:
            print(f"caltreat: could not make the HTTPS certificate: {r.stderr.strip()}", file=sys.stderr)
            return "failed"
        tmp.replace(cert)  # in one step: a server reading it never sees half a file
        print(f"caltreat: made a self-signed HTTPS certificate for {', '.join(names)}", file=sys.stderr)
        return "made"
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"caltreat: no HTTPS ({e})", file=sys.stderr)
        return "failed"


def bind(port):
    """A listening socket on every address, IPv6 and IPv4 when the Pi has IPv6 (mDNS also hands out the Pi's
    IPv6 addresses), else IPv4 only. Raises OSError (EACCES for ports below the unprivileged start)."""
    for family in (socket.AF_INET6, socket.AF_INET):
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                s.bind(("::", port))
            else:
                s.bind(("0.0.0.0", port))
            s.listen(2048)
            s.setblocking(False)
            return s
        except OSError as e:
            s.close()
            if family == socket.AF_INET6 and e.errno in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                continue
            raise
    raise OSError(errno.EAFNOSUPPORT, "no IPv4 or IPv6")


def why(port, e):
    if isinstance(e, OSError) and e.errno == errno.EACCES:
        return (f"port {port} needs the one-time network setup on the Pi (sudo ~/caltreat/setup/network.sh, or "
                f"sysctl net.ipv4.ip_unprivileged_port_start=80)")
    return f"port {port}: {e}"


class _QuietServer(uvicorn.Server):
    """Runs beside NiceGUI's server in the same event loop; NiceGUI's server handles the signals."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


class Listener:
    """One extra port (http, or https with the certificate), started again until it works."""

    def __init__(self, app, port, tls=None):
        self.app, self.port, self.tls = app, port, tls  # tls: (cert path, key path) or None
        self.server = None
        self.stopping = False
        self.task = None
        self.error = ""

    @property
    def up(self):
        return bool(self.server and self.server.started)

    async def run(self):
        delay = 2.0
        while not self.stopping:
            try:
                sock = bind(self.port)
            except OSError as e:
                msg = why(self.port, e)
                if msg != self.error:  # say it once, not every retry
                    print(f"caltreat: {'https' if self.tls else 'http'} not on {msg}; trying again", file=sys.stderr)
                self.error = msg
            else:
                ssl = dict(ssl_certfile=str(self.tls[0]), ssl_keyfile=str(self.tls[1])) if self.tls else {}
                config = uvicorn.Config(self.app, lifespan="off", log_level="warning", ws="wsproto", **ssl)
                self.server = _QuietServer(config)
                if self.error:
                    print(f"caltreat: {'https' if self.tls else 'http'} on port {self.port} now works", file=sys.stderr)
                self.error = ""
                delay = 2.0
                try:
                    await self.server.serve(sockets=[sock])
                except (SystemExit, OSError) as e:
                    self.error = f"port {self.port}: {e}"
                    print(f"caltreat: {self.error}", file=sys.stderr)
                finally:
                    sock.close()
            if self.stopping:
                break
            await asyncio.sleep(delay)
            delay = min(60.0, delay * 2)

    def reload_cert(self):
        """Use the certificate file again (new connections get the new one)."""
        cfg = self.server.config if self.server else None
        if self.tls and cfg is not None and getattr(cfg, "ssl", None) is not None:
            cfg.ssl.load_cert_chain(str(self.tls[0]), str(self.tls[1]))
            return True
        return False


class Listeners:
    def __init__(self, app, cert_folder, env=None):
        self.app = app
        self.folder = Path(cert_folder)
        self.cert, self.key = self.folder / "https-cert.pem", self.folder / "https-key.pem"
        self.main, http, https = ports_from_env(env)
        self.http = [Listener(app, p) for p in http]
        self.https = [Listener(app, p, (self.cert, self.key)) for p in https]
        self.tasks = []
        self.watch = None
        self.ips = None

    async def start(self):
        loop = asyncio.get_running_loop()
        for lst in self.http:
            self.tasks.append(loop.create_task(lst.run()))
        if self.https:
            self.ips = await loop.run_in_executor(None, current_ips)
            ok = await loop.run_in_executor(None, ensure_cert, self.folder, None, self.ips) != "failed"
            if ok:
                for lst in self.https:
                    self.tasks.append(loop.create_task(lst.run()))
                self.watch = loop.create_task(self._watch_addresses())

    async def _watch_addresses(self):
        loop = asyncio.get_running_loop()
        checks = 0
        while True:
            await asyncio.sleep(CHECK_EVERY)
            checks += 1
            with contextlib.suppress(Exception):
                await self.check_cert(loop, expiry=checks % 60 == 0)  # expiry about once an hour

    async def check_cert(self, loop=None, expiry=False):
        """New addresses (or, with expiry, a certificate near its end): make the certificate again and switch the
        https servers to it. Returns True when it made a new one."""
        loop = loop or asyncio.get_running_loop()
        ips = await loop.run_in_executor(None, current_ips)
        if not expiry and set(ips) <= set(self.ips or []):
            return False
        self.ips = ips
        if await loop.run_in_executor(None, ensure_cert, self.folder, None, ips) != "made":
            return False
        for lst in self.https:
            with contextlib.suppress(Exception):
                lst.reload_cert()
        return True

    def stop(self):
        for lst in [*self.http, *self.https]:
            lst.stopping = True
            if lst.server:
                lst.server.should_exit = True
        if self.watch:
            self.watch.cancel()

    def https_url(self, host):
        """The intercom's https address on the host the page was opened by: no port when 443 works."""
        host = host or "caltreat.local"
        if ":" in host:
            host = f"[{host}]"  # IPv6 address
        for lst in self.https:
            if lst.up:
                return f"https://{host}/" if lst.port == 443 else f"https://{host}:{lst.port}/"
        return ""

    def status(self):
        """{port: "" (works) or why not} for every port, the main one included."""
        out = {self.main: ""}
        for lst in [*self.http, *self.https]:
            out[lst.port] = "" if lst.up else (lst.error or "starting")
        return out
