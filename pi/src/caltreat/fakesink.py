"""Stands in for `aplay` without sound hardware: reads raw S16_LE frames from stdin at real time (allowing 60 ms of
buffer, as the real player) and, with a directory, keeps them in <dir>/<name>-<n>.raw plus <name>-<n>.json, which
records when the first frame was taken (time.time()), so tests can tell when each sample "played".

    python -m caltreat.fakesink <dir or -> <name> <rate> <channels>
"""

import itertools
import json
import os
import sys
import time

BUFFER = 0.06  # s


def main():
    out_dir, name, rate, channels = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    frame = 2 * channels
    out = None
    if out_dir != "-":
        for n in itertools.count():
            path = os.path.join(out_dir, f"{name}-{n}.raw")
            try:
                out = open(path, "xb")
                break
            except FileExistsError:
                continue
    stdin = sys.stdin.buffer
    start = None
    frames = 0
    while True:
        data = stdin.read1(4096) if hasattr(stdin, "read1") else stdin.read(4096)
        if not data:
            break
        if start is None:
            start = time.time()
            t0 = time.monotonic()
            if out:
                with open(path[:-4] + ".json", "w") as f:
                    json.dump({"start": start, "rate": rate, "channels": channels}, f)
        if out:
            out.write(data)
            out.flush()
        frames += len(data) // frame
        ahead = frames / rate - (time.monotonic() - t0)  # played so far vs written
        if ahead > BUFFER:
            time.sleep(ahead - BUFFER)
    if out:
        out.close()


if __name__ == "__main__":
    main()
