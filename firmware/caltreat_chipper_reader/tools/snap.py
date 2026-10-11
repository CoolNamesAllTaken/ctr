#!/usr/bin/env python3
"""
Grab a screenshot from the reader over the serial console.

    python3 tools/snap.py /dev/cu.wchusbserial110 out.png [--top|--fb] [command ...]

Optional commands (e.g. "tap", "low") are sent first; the shot is taken after
a short settle delay. Needs only pyserial (run it with the ESP-IDF venv python).
"""
import base64
import re
import struct
import sys
import time
import zlib

import serial

B64_LINE = re.compile(r"[A-Za-z0-9+/=]+")


def write_png(path, w, h, rgb):
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    rows = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(rows, 6)))
        f.write(chunk(b"IEND", b""))


def main():
    port, out, cmds = sys.argv[1], sys.argv[2], sys.argv[3:]
    snap_cmd = b"snap\n"
    if "--top" in cmds:  # capture the overlay layer (debug sheet) instead
        cmds.remove("--top")
        snap_cmd = b"snap top\n"
    if "--fb" in cmds:  # read back the frame buffer actually on the panel
        cmds.remove("--fb")
        snap_cmd = b"snap fb\n"
    s = serial.Serial(port, 115200, timeout=0.5)
    s.dtr = False
    s.rts = False
    for attempt in range(3):
        for c in cmds:  # re-sent on retry: a result screen times out during a dump
            s.write((c + "\n").encode())
            time.sleep(0.4)
        time.sleep(0.6)
        try:
            w, h, px = grab(s, snap_cmd)
            break
        except (ValueError, struct.error) as e:  # occasional corrupted byte on the link
            print(f"retrying ({e})")
    else:
        sys.exit("screenshot failed")
    write_png(out, w, h, px)
    print(f"saved {out} ({w}x{h})")


def grab(s, snap_cmd):
    s.reset_input_buffer()
    s.write(snap_cmd)

    w = h = None
    chunks = []
    deadline = time.time() + 120
    while time.time() < deadline:
        line = s.readline().decode("ascii", "replace").strip()
        if line.startswith("SNAP-BEGIN"):
            _, w, h = line.split()
            w, h = int(w), int(h)
        elif line == "SNAP-END":
            break
        elif w is not None and B64_LINE.fullmatch(line):  # skips interleaved log lines
            chunks.append(line)
    raw = base64.b64decode("".join(chunks), validate=True)

    px = bytearray()
    for n, c in struct.iter_unpack("<HH", raw):
        r, g, b = (c >> 11) << 3, ((c >> 5) & 0x3F) << 2, (c & 0x1F) << 3
        px += bytes((r, g, b)) * n
    if len(px) != w * h * 3:
        raise ValueError("pixel count mismatch")
    return w, h, bytes(px)


if __name__ == "__main__":
    main()
