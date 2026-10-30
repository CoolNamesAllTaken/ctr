#!/usr/bin/env python3
"""Dump a MIFARE Classic 1K/4K card through the RC522 PN532 bridge.

Authenticates each sector with a key you supply (default: the factory key
FFFFFFFFFFFF) and reads every block. Prints a hex dump and optionally writes a
.mfd (raw 1K/4K binary, the format libnfc's nfc-mfclassic and NFCToolsGUI use).

    python mfc_dump.py --port /dev/tty.usbmodemXXXX1
    python mfc_dump.py --port ... --key A0A1A2A3A4A5 --out card.mfd
"""
import argparse
import sys

import serial
from adafruit_pn532.uart import PN532_UART

MIFARE_CMD_AUTH_A = 0x60
MIFARE_CMD_AUTH_B = 0x61


def blocks_for(size_k: int) -> int:
    return 64 if size_k == 1 else 256  # 1K = 64 blocks, 4K = 256 blocks


def sector_first_block(block: int) -> int:
    """First block of the sector that `block` belongs to (for auth)."""
    if block < 128:
        return (block // 4) * 4
    return 128 + ((block - 128) // 16) * 16


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--key", default="FFFFFFFFFFFF", help="6-byte key as hex")
    ap.add_argument("--key-a", action="store_true", help="authenticate with key A only")
    ap.add_argument("--key-b", action="store_true", help="authenticate with key B only")
    ap.add_argument("--size", type=int, choices=(1, 4), default=1, help="card size in K")
    ap.add_argument("--out", help="write raw dump to this .mfd file")
    args = ap.parse_args()

    key = bytes.fromhex(args.key)
    if len(key) != 6:
        ap.error("key must be 6 bytes (12 hex chars)")

    # Which keys to try, in order. Default: try A then B (cards vary on which
    # one carries the factory value). --key-a / --key-b restrict to one.
    if args.key_a:
        key_cmds = [("A", MIFARE_CMD_AUTH_A)]
    elif args.key_b:
        key_cmds = [("B", MIFARE_CMD_AUTH_B)]
    else:
        key_cmds = [("A", MIFARE_CMD_AUTH_A), ("B", MIFARE_CMD_AUTH_B)]

    uart = serial.Serial(args.port, args.baud, timeout=1)
    pn532 = PN532_UART(uart, debug=False)
    pn532.SAM_configuration()

    uid = pn532.read_passive_target(timeout=2)
    if uid is None:
        print("No card detected.", file=sys.stderr)
        return 1
    print(f"Card UID: {uid.hex().upper()}")

    n = blocks_for(args.size)
    dump = bytearray(n * 16)

    # Group blocks by sector and authenticate once per sector. A fresh select is
    # required before each sector's auth: after a read (or a failed auth) the
    # card leaves the authenticated state, and the RC522/PN532 needs to
    # re-run anticollision+select before the next crypto1 handshake.
    sector_trailers = sorted({sector_first_block(b) for b in range(n)})
    for sb in sector_trailers:
        blocks = [b for b in range(n) if sector_first_block(b) == sb]

        # (Re)select the card so auth starts from a clean state.
        uid = pn532.read_passive_target(timeout=1)
        if uid is None:
            print(f"sector @ {sb:3d}: card lost")
            continue

        used = None
        for name, cmd in key_cmds:
            if pn532.mifare_classic_authenticate_block(uid, sb, cmd, key):
                used = name
                break
            # a failed auth drops the card; re-select before trying the next key
            uid = pn532.read_passive_target(timeout=1)
            if uid is None:
                break
        if used is None:
            print(f"sector @ {sb:3d}: AUTH FAILED (key {args.key})")
            continue

        for block in blocks:
            data = pn532.mifare_classic_read_block(block)
            if data is None:
                print(f"block {block:3d}: read failed")
                continue
            dump[block * 16:(block + 1) * 16] = data
            print(f"block {block:3d} (key {used}): {data.hex().upper()}")

    if args.out:
        with open(args.out, "wb") as f:
            f.write(dump)
        print(f"Wrote {len(dump)} bytes to {args.out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nbye")
