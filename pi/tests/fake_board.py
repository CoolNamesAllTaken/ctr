"""A fake CalTreat lighting controller for tests: Modbus TCP with just what the app uses (function codes 4, 15, 16
and 43; registers from firmware/caltreat_lighting_controller/src/regmap.h). Records the coils and pin blocks written.
Like the real board, a dropped connection puts every pin at its failsafe default (HR 2000 + 8 * pin, if written).

    board = FakeBoard("127.0.1.10", 15020, uid="E6614103E7A1B2C3").start()
    board.coils  # [bool] x 8, as last written by the app
    board.stop()

Run it by hand to try the settings page without hardware:
    python pi/tests/fake_board.py 127.0.1.10 15020 [UID]
"""

import socket
import struct
import sys
import threading


class FakeBoard:
    def __init__(self, host, port, uid="E6614103E7A1B2C3", mac="28:CD:C1:0A:0B:0C"):
        self.host, self.port, self.uid, self.mac = host, port, uid, mac
        self.coils = [False] * 30
        self.holding = {}
        self.failsafe = True  # as after boot, until the pins are set up
        self.requests = []
        self.sock = None
        self.running = False

    def start(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(4)
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _recv(self, conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def input_regs(self):
        uid = bytes.fromhex(self.uid.rjust(16, "0"))
        mac = bytes.fromhex(self.mac.replace(":", ""))
        regs = [0] * 32
        regs[0] = 1
        regs[3:7] = struct.unpack(">4H", uid)
        regs[10] = 0x0002 if self.failsafe else 0
        regs[16:19] = struct.unpack(">3H", mac)
        return regs

    def handle(self, pdu):
        fc = pdu[0]
        self.requests.append(fc)
        if fc == 4:
            addr, n = struct.unpack(">HH", pdu[1:5])
            regs = self.input_regs()
            vals = [regs[a] if a < len(regs) else 0 for a in range(addr, addr + n)]
            return struct.pack(">BB", 4, 2 * n) + struct.pack(f">{n}H", *vals)
        if fc == 15:
            addr, n, nb = struct.unpack(">HHB", pdu[1:6])
            bits = int.from_bytes(pdu[6:6 + nb], "little")
            for i in range(n):
                self.coils[addr + i] = bool(bits >> i & 1)
            return struct.pack(">BHH", 15, addr, n)
        if fc == 16:
            addr, n, _ = struct.unpack(">HHB", pdu[1:6])
            vals = struct.unpack(f">{n}H", pdu[6:6 + 2 * n])
            for i, v in enumerate(vals):
                self.holding[addr + i] = v
            if 1000 <= addr < 1000 + 16 * 30 and (addr - 1000) % 16 == 0:  # a pin block: its level flag sets the pin
                gpio = (addr - 1000) // 16
                self.coils[gpio] = bool(vals[3] & 0x10)
                self.failsafe = False
            return struct.pack(">BHH", 16, addr, n)
        if fc == 43:
            objs = {0: b"CalTreat", 1: b"lighting-controller", 2: b"0.5.0"}
            body = bytes([43, 14, 1, 0x01, 0, 0, len(objs)])
            for k, v in objs.items():
                body += bytes([k, len(v)]) + v
            return body
        return bytes([fc | 0x80, 1])

    def _serve(self, conn):
        try:
            while self.running:
                tid, pid, length, unit = struct.unpack(">HHHB", self._recv(conn, 7))
                reply = self.handle(self._recv(conn, length - 1))
                conn.sendall(struct.pack(">HHHB", tid, 0, len(reply) + 1, unit) + reply)
        except (OSError, ConnectionError, struct.error):
            pass
        finally:
            conn.close()
            self.failsafe = True  # the real board falls back when the connection drops
            for gpio in range(8):
                flags = self.holding.get(2000 + 8 * gpio + 3)
                self.coils[gpio] = bool(flags & 0x10) if flags is not None else False


if __name__ == "__main__":
    b = FakeBoard(sys.argv[1], int(sys.argv[2]), *sys.argv[3:4]).start()
    print(f"fake board {b.uid} on {b.host}:{b.port}; Ctrl-C to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        b.stop()
