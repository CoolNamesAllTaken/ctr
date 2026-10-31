"""The part of lcctl's `Board` (client.py) that ota.py uses, on a plain socket so the app needs no pymodbus: function
codes 4, 16 and 21 (write file record). Not a copy; ota.py reaches the board only through this."""

from __future__ import annotations

import os
import socket
import struct
from dataclasses import dataclass

from . import regmap as rm

PORT = int(os.environ.get("CALTREAT_BOARD_PORT", rm.MODBUS_PORT))  # as the app (tests use another port)


class ModbusError(OSError):
    """The board answered with a Modbus exception (or not at all)."""

    def __init__(self, what: str, code: int | None = None):
        super().__init__(f"{what}: " + (f"exception {code}" if code is not None else "no valid response"))
        self.code = code


@dataclass
class FileRecord:
    """A write file record sub-request (pymodbus.pdu.file_message.FileRecord's fields)."""
    file_number: int = 0
    record_number: int = 0
    record_data: bytes = b""


class _Reply:
    def __init__(self, error=None):
        self.error = error

    def isError(self):
        return self.error is not None


def parse_info(r) -> dict:
    """lcctl Board.info() from input registers 0-31."""
    flags = r[rm.IR_FLAGS]
    fw = rm.u32(r, rm.IR_FW_VERSION)
    return {
        "regmap": r[rm.IR_MAP_VERSION],
        "fw": fw,
        "fw_version": rm.version_str(fw),
        "uid": rm.reg_bytes(r[rm.IR_UID:rm.IR_UID + 4]).hex().upper(),
        "boot_partition": {0: "A", 1: "B"}.get(r[rm.IR_BOOT_PARTITION], "?"),
        "trial": bool(flags & rm.IR_FLAG_TRIAL),
        "failsafe": bool(flags & rm.IR_FLAG_FAILSAFE),
        "ota_busy": bool(flags & rm.IR_FLAG_OTA_BUSY),
        "uptime_ms": rm.u32(r, rm.IR_UPTIME_MS),
        "mac": ":".join(f"{b:02X}" for b in rm.reg_bytes(r[rm.IR_MAC:rm.IR_MAC + 3])),
        "ip": ".".join(str(b) for b in rm.reg_bytes(r[rm.IR_IP:rm.IR_IP + 2])),
    }


class Board:
    def __init__(self, host: str, port: int | None = None, timeout: float = 2.0, retries: int = 1):
        self.host, self.port, self.timeout = host, PORT if port is None else port, timeout
        self.sock = None
        self.tid = 0
        self.client = self  # lcctl's ota.py calls board.client.write_file_record()

    def connect(self) -> "Board":
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise ConnectionError(f"cannot connect to {self.host}:{self.port}: {e}") from e
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return self

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self) -> "Board":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed by the board")
            buf += chunk
        return buf

    def _request(self, pdu: bytes, what: str) -> bytes:
        if self.sock is None:
            raise ConnectionError(f"{what}: not connected")
        self.tid = (self.tid + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, 1) + pdu)
        tid, _, length, _ = struct.unpack(">HHHB", self._recv(7))
        body = self._recv(length - 1)
        if tid != self.tid or not body:
            raise ModbusError(what)
        if body[0] == pdu[0] | 0x80:
            raise ModbusError(what, body[1])
        if body[0] != pdu[0]:
            raise ModbusError(what)
        return body

    def read_input(self, address: int, count: int = 1) -> list[int]:
        body = self._request(struct.pack(">BHH", 4, address, count), f"read IR {address}")
        return list(struct.unpack(f">{count}H", body[2:2 + 2 * count]))

    def write_holding(self, address: int, values) -> None:
        values = list(values)
        self._request(struct.pack(">BHHB", 16, address, len(values), 2 * len(values)) +
                      struct.pack(f">{len(values)}H", *values), f"write HR {address}")

    def write_file_record(self, records) -> _Reply:
        sub = b"".join(struct.pack(">BHHH", 6, r.file_number, r.record_number, len(r.record_data) // 2) +
                       r.record_data for r in records)
        try:
            self._request(struct.pack(">BB", 21, len(sub)) + sub, "write file record")
        except ModbusError as e:
            return _Reply(e)
        return _Reply()

    def info(self) -> dict:
        return parse_info(self.read_input(rm.IR_MAP_VERSION, rm.IR_INFO_COUNT))
