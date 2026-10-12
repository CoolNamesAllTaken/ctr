"""Find boards on the network from DHCP leases, confirmed over Modbus.

Lease sources:
  macOS Internet Sharing (bench): /var/db/dhcpd_leases
  dnsmasq (Raspberry Pi):         /var/lib/misc/dnsmasq.leases
The CH9120 registers with the DHCP hostname "CH9120".
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from .client import Board

LEASE_FILES = ("/var/db/dhcpd_leases", "/var/lib/misc/dnsmasq.leases")


@dataclass
class Lease:
    ip: str
    mac: str
    name: str


def _norm_mac(mac: str) -> str:
    return ":".join(f"{int(p, 16):02x}" for p in mac.split(":"))


def read_leases(path: str) -> list[Lease]:
    with open(path) as f:
        text = f.read()
    leases = []
    if "{" in text:  # macOS bootpd
        for block in re.findall(r"\{(.*?)\}", text, re.S):
            fields = dict(re.findall(r"^\s*(\w+)=(.*)$", block, re.M))
            if "ip_address" in fields and "hw_address" in fields:
                mac = fields["hw_address"].split(",", 1)[-1]
                leases.append(Lease(fields["ip_address"], _norm_mac(mac), fields.get("name", "")))
    else:  # dnsmasq: expiry mac ip name client-id
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                leases.append(Lease(parts[2], _norm_mac(parts[1]), parts[3]))
    return leases


def candidate_leases() -> list[Lease]:
    out = []
    for path in LEASE_FILES:
        if os.path.exists(path):
            out += read_leases(path)
    return out


def discover(timeout: float = 1.0) -> list[dict]:
    """Return info for every leased address that answers as a lighting controller."""
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)   # leases that don't answer are normal
    boards = []
    for lease in candidate_leases():
        try:
            with Board(lease.ip, timeout=timeout, retries=0) as b:
                ident = b.device_id()
                if ident.get(0x01) != "lighting-controller":
                    continue
                info = b.info()
        except Exception:  # noqa: BLE001 - anything that doesn't answer like a board
            continue
        boards.append({"ip": lease.ip, "lease_mac": lease.mac, "uid": info["uid"],
                       "fw_version": info["fw_version"], "partition": info["boot_partition"]})
    return boards
