#!/usr/bin/env python3
"""Continuously read and print the UID of any ISO14443A tag presented to the
RC522-backed PN532 bridge.

The firmware exposes two USB CDC ports; pass the *second* one (labelled
"PN532 Emulation") as --port. On macOS it's typically the higher-numbered
/dev/tty.usbmodemXXXX3.

    python read_uid.py --port /dev/tty.usbmodemXXXX3
"""
import argparse
import sys
import time

import serial
from adafruit_pn532.uart import PN532_UART


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True, help="PN532 CDC serial port")
    ap.add_argument("--baud", type=int, default=115200,
                    help="baud rate (ignored by USB CDC, but pyserial wants one)")
    args = ap.parse_args()

    uart = serial.Serial(args.port, args.baud, timeout=1)
    pn532 = PN532_UART(uart, debug=False)

    ic, ver, rev, support = pn532.firmware_version
    print(f"Found PN532 (emulated): IC=0x{ic:02X} v{ver}.{rev} support=0x{support:02X}")
    pn532.SAM_configuration()

    print("Waiting for a card... (Ctrl-C to stop)")
    last = None
    while True:
        uid = pn532.read_passive_target(timeout=0.5)
        if uid is None:
            last = None
            continue
        hexuid = uid.hex().upper()
        if hexuid != last:
            print(f"UID: {hexuid}  ({len(uid)} bytes)")
            last = hexuid
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nbye")
