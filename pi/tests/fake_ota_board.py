"""A fake lighting controller that also takes firmware updates (src/updater.c, src/regmap.h): firmware version, the
OTA status input registers (400-404), the OTA control holding registers (3000-3020) and write file record (FC 21).
Like the real board it serves one client at a time (the CH9120): a second connection is closed at once.

    board = FakeOtaBoard("127.0.1.11", 15021, uid="E6614103E7A1B2D4", fw=0x000600).start()
    board.reboot_s = 1.0      # how long ACTIVATE takes before it answers again
    board.never_return = True # ACTIVATE and stay away (board.offline_until = 0 brings it back)
    board.rollback = True     # come back with the old image (the new one didn't buy)
    board.corrupt = True      # flip a received byte: VERIFY fails (hash mismatch)
    board.delay_s = 0.01      # per file record, to watch the progress bar

    python pi/tests/fake_ota_board.py 127.0.1.11 15020 [UID] [X.Y.Z]   # by hand
"""

import hashlib
import struct
import sys
import threading
import time

from fake_board import FakeBoard

IDLE, RECEIVING, VERIFIED, FAILED = range(4)
ERR_NONE, ERR_STATE, ERR_LENGTH, ERR_SAME, ERR_TRIAL, ERR_FLASH, ERR_HASH, ERR_SEQ = range(8)
FW_INFO_MAGIC = struct.pack("<II", 0x45325052, 0x4F464E49)


def make_image(version, size=20000):
    """Bytes that lcctl.image.load accepts as a firmware image of `version` (0x00MMmmpp)."""
    body = bytes((i * 7 + version) & 0xFF for i in range(size))
    return body[:256] + FW_INFO_MAGIC + struct.pack("<I", version) + body[268:]


class FakeOtaBoard(FakeBoard):
    def __init__(self, host, port, uid="E6614103E7A1B2D4", mac="28:CD:C1:0A:0B:0D", fw=0x000600):
        super().__init__(host, port, uid, mac)
        self.fw = fw
        self.partition = 0
        self.trial = False
        self.ota = dict(state=IDLE, error=ERR_NONE, length=0, version=0, sha=b"", data=bytearray())
        self.reboot_s = 0.5
        self.never_return = self.rollback = self.corrupt = False
        self.delay_s = 0.0
        self.offline_until = 0.0
        self.clients = 0  # open connections (at most one)
        self.refused = []  # (time, OTA state) of connections closed because one was open
        self.log = []  # (time, event): connect, disconnect, begin, verify, activate, abort, back, buy
        self.lock = threading.Lock()

    # ---- connections: one at a time, none while "rebooting" -------------------------------------------------
    def stop(self):
        self.running = False
        for sock in (self.sock, getattr(self, "conn", None)):
            try:
                sock.shutdown(2)  # wakes the accept() / recv() blocked on it, so the port is free at once
            except (OSError, AttributeError):
                pass
        super().stop()

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with self.lock:
                offline = time.monotonic() < self.offline_until
                busy = self.clients > 0
                if not (offline or busy):
                    self.clients += 1
                    self.log.append((time.monotonic(), "connect"))
            if offline or busy:
                if busy and not offline:
                    self.refused.append((time.monotonic(), self.ota["state"]))
                conn.close()
                continue
            threading.Thread(target=self._serve_one, args=(conn,), daemon=True).start()

    def _serve_one(self, conn):
        self.conn = conn
        try:
            self._serve(conn)
        finally:
            with self.lock:
                self.clients -= 1
                self.log.append((time.monotonic(), "disconnect"))

    def _drop(self):
        """Close the current connection (as the board does when it reboots)."""
        try:
            self.conn.shutdown(2)
        except OSError:
            pass

    # ---- registers --------------------------------------------------------------------------------------------
    def input_regs(self):
        regs = super().input_regs()
        regs[1:3] = [self.fw >> 16, self.fw & 0xFFFF]
        regs[7] = self.partition
        regs[10] |= (0x0001 if self.trial else 0) | (0x0004 if self.ota["state"] == RECEIVING else 0)
        return regs

    def ota_regs(self):
        o = self.ota
        n = len(o["data"])
        return [o["state"], o["error"], n >> 16, n & 0xFFFF, 1 - self.partition]

    def _fail(self, err):
        self.ota.update(state=FAILED, error=err)
        return bytes([0x90, 3])  # FC 16 exception: illegal data value

    def handle(self, pdu):
        fc = pdu[0]
        if self.trial and fc in (4, 16, 21, 43) and not self.rollback:
            self.trial = False  # the first request after an update buys the image
            self.log.append((time.monotonic(), "buy"))
        if fc == 4:
            addr, n = struct.unpack(">HH", pdu[1:5])
            if addr >= 400:
                regs = self.ota_regs()
                vals = [regs[a - 400] if 400 <= a < 405 else 0 for a in range(addr, addr + n)]
                self.requests.append(fc)
                return struct.pack(">BB", 4, 2 * n) + struct.pack(f">{n}H", *vals)
        if fc == 16:
            addr, n, _ = struct.unpack(">HHB", pdu[1:6])
            if addr == 3000:
                self.requests.append(fc)
                return self.command(list(struct.unpack(f">{n}H", pdu[6:6 + 2 * n])), pdu[:5])
        if fc == 21:
            self.requests.append(fc)
            return self.write_record(pdu)
        return super().handle(pdu)

    def command(self, vals, echo):
        o, cmd = self.ota, vals[0]
        if cmd == 1:  # BEGIN: length, major, minor, sha256
            self.log.append((time.monotonic(), "begin"))
            o.update(state=IDLE, error=ERR_NONE, data=bytearray())
            if self.trial:
                return self._fail(ERR_TRIAL)
            length = (vals[1] << 16) | vals[2]
            version = (vals[3] << 16) | vals[4]  # major, (minor << 8) | patch
            if version == self.fw:
                return self._fail(ERR_SAME)
            o.update(state=RECEIVING, length=length, version=version, sha=b"".join(struct.pack(">H", v) for v in vals[5:21]))
        elif cmd == 2:  # VERIFY
            self.log.append((time.monotonic(), "verify"))
            if o["state"] == VERIFIED:
                pass
            elif o["state"] != RECEIVING or len(o["data"]) != o["length"]:
                return bytes([0x90, 3])
            else:
                data = bytearray(o["data"])
                if self.corrupt:
                    data[len(data) // 2] ^= 0xFF
                if hashlib.sha256(data).digest() != o["sha"]:
                    return self._fail(ERR_HASH)
                o["state"] = VERIFIED
        elif cmd == 3:  # ACTIVATE: answer, then reboot into the new image on trial
            self.log.append((time.monotonic(), "activate"))
            if o["state"] != VERIFIED:
                return bytes([0x90, 3])
            threading.Timer(0.02, self._reboot).start()
        elif cmd == 4:  # ABORT
            self.log.append((time.monotonic(), "abort"))
            o.update(state=IDLE, error=ERR_NONE, data=bytearray())
        else:
            return bytes([0x90, 3])
        return echo

    def _reboot(self):
        self.offline_until = float("inf") if self.never_return else time.monotonic() + self.reboot_s
        self._drop()
        if not self.rollback:
            self.fw, self.partition, self.trial = self.ota["version"], 1 - self.partition, True
        self.ota.update(state=IDLE, error=ERR_NONE, data=bytearray())
        self.failsafe = True
        self.coils = [False] * 30  # pins back to their defaults
        self.log.append((time.monotonic(), "back"))

    def write_record(self, pdu):
        o = self.ota
        if self.delay_s:
            time.sleep(self.delay_s)
        _, nbytes, ref, file_no, rec_no, nregs = struct.unpack(">BBBHHH", pdu[:9])
        data = pdu[9:9 + 2 * nregs]
        if o["state"] != RECEIVING:
            return bytes([0x95, 3])
        offset = (file_no - 1) * 4096 + rec_no * 2
        if offset + len(data) <= len(o["data"]):
            return pdu[:2 + nbytes]
        if offset != len(o["data"]):
            o["error"] = ERR_SEQ
            return bytes([0x95, 3])
        o["data"] += data[:o["length"] - offset]  # the last record is padded to whole registers
        return pdu[:2 + nbytes]


if __name__ == "__main__":
    fw = 0x000600
    if len(sys.argv) > 4:
        a, b, c = (int(x) for x in sys.argv[4].split("."))
        fw = (a << 16) | (b << 8) | c
    board = FakeOtaBoard(sys.argv[1], int(sys.argv[2]), *(sys.argv[3:4] or ["E6614103E7A1B2D4"]), fw=fw).start()
    board.delay_s = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    print(f"fake OTA board {board.uid} fw {fw:06x} on {board.host}:{board.port}; Ctrl-C to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        board.stop()
