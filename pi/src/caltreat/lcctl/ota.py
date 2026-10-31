# Vendored from firmware/caltreat_lighting_controller/tools/src/lcctl/ota.py (deploy.sh copies only pi/).
# Don't edit: change the original, then run `python pi/tests/test_lcfw.py --sync`.
"""Firmware update over Modbus (see src/regmap.h, src/updater.c)."""

from __future__ import annotations

import logging
import time
from typing import Callable

from .client import FileRecord

from . import regmap as rm
from .client import Board, ModbusError
from .image import Image

SECTOR = 4096
RECORD_BYTES = 240          # 120 registers per FC 21 request

OTA_IDLE, OTA_RECEIVING, OTA_VERIFIED, OTA_FAILED = range(4)
OTA_ERRORS = {
    0: "none", 1: "bad state", 2: "bad length", 3: "same version", 4: "running image on trial",
    5: "flash error", 6: "hash mismatch", 7: "out of sequence",
}
CMD_BEGIN, CMD_VERIFY, CMD_ACTIVATE, CMD_ABORT = 1, 2, 3, 4


class UpdateError(Exception):
    pass


def ota_status(board: Board) -> dict:
    r = board.read_input(rm.IR_OTA_BASE, rm.IR_OTA_COUNT)
    return {"state": r[0], "error": OTA_ERRORS.get(r[1], str(r[1])), "received": rm.u32(r, 2), "target": r[4]}


def check_version(running: int, image: Image, allow_downgrade: bool) -> None:
    if image.version == running:
        raise UpdateError(f"board already runs {rm.version_str(running)}")
    if image.version < running and not allow_downgrade:
        raise UpdateError(f"{image.version_str} is older than the running {rm.version_str(running)}; "
                          "pass allow_downgrade to revert")


def _command(board: Board, regs: list[int]) -> None:
    try:
        board.write_holding(rm.HR_OTA_BASE, regs)
    except ModbusError as e:
        raise UpdateError(f"update command {regs[0]} refused: {ota_status(board)['error']}") from e


def begin(board: Board, image: Image) -> None:
    sha = image.sha256
    regs = [CMD_BEGIN, *rm.split_u32(len(image.data)), image.major, image.minor]
    regs += [(sha[i] << 8) | sha[i + 1] for i in range(0, 32, 2)]
    _command(board, regs)


def send(board: Board, data: bytes, progress: Callable[[int, int], None] | None = None,
         stop_at: int | None = None) -> None:
    """Stream the image. `stop_at` (tests) stops early to simulate an interruption."""
    end = len(data) if stop_at is None else min(stop_at, len(data))
    offset = 0
    while offset < end:
        n = min(RECORD_BYTES, SECTOR - offset % SECTOR, len(data) - offset)
        chunk = data[offset:offset + n]
        if len(chunk) % 2:
            chunk += b"\xff"
        rec = FileRecord(file_number=1 + offset // SECTOR, record_number=(offset % SECTOR) // 2, record_data=chunk)
        rr = board.client.write_file_record([rec])
        if rr is None or rr.isError():
            raise UpdateError(f"write at {offset} refused: {ota_status(board)['error']}")
        offset += n
        if progress:
            progress(offset, len(data))


def verify(board: Board) -> None:
    _command(board, [CMD_VERIFY])


def activate(board: Board) -> None:
    _command(board, [CMD_ACTIVATE])
    board.close()


def abort(board: Board) -> None:
    _command(board, [CMD_ABORT])


def wait_for_board(host: str, timeout: float = 30.0) -> Board:
    """Reconnect after a reboot. The first request also makes a trial image buy itself."""
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)   # refused/timed out while rebooting
    deadline = time.monotonic() + timeout
    while True:
        b = Board(host, timeout=1.0, retries=0)
        try:
            b.connect()
            b.info()
            return b
        except Exception:
            b.close()
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def update(host: str, image: Image, allow_downgrade: bool = False,
           progress: Callable[[int, int], None] | None = None, log: Callable[[str], None] = print) -> dict:
    """Full update: send, verify, activate, then reconnect so the new image buys itself."""
    with Board(host) as b:
        info = b.info()
        if info["trial"]:
            raise UpdateError("the running image is still on trial; reconnect to let it buy first")
        running = rm.parse_version(info["fw_version"])
        check_version(running, image, allow_downgrade)
        log(f"{host}: {info['fw_version']} on partition {info['boot_partition']} -> {image.version_str}")
        t0 = time.monotonic()
        begin(b, image)
        send(b, image.data, progress)
        verify(b)
        log(f"sent and verified {len(image.data)} bytes in {time.monotonic() - t0:.1f} s, activating")
        old_partition = info["boot_partition"]
        activate(b)

    time.sleep(1.0)
    b = wait_for_board(host)
    try:
        # Our reconnect was the board's first request: it buys itself now
        deadline = time.monotonic() + 10
        while (info := b.info())["trial"] and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        b.close()
    if info["fw_version"] != image.version_str or info["trial"]:
        raise UpdateError(f"board came back running {info['fw_version']} (trial {info['trial']}): update rolled back?")
    log(f"running {info['fw_version']} from partition {info['boot_partition']} (was {old_partition})")
    return info
