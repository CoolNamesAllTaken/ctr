"""PlatformIO "Upload": install the app over USB with picotool.

    upload.py <picotool> <build_dir>

Works on a blank RP2350 and on one already running this app:
  1. get the board into BOOTSEL (a running app is reset over USB)
  2. write the partition table and reboot, so the boot ROM loads it
  3. load the firmware and start it
"""

import os
import subprocess
import sys
import time


def picotool(*args, check=True):
    r = subprocess.run([PICOTOOL, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"picotool {' '.join(args)} failed:\n{r.stdout}{r.stderr}")
    return r


def wait_for_bootsel():
    for _ in range(30):
        if picotool("info", check=False).returncode == 0:
            return
        time.sleep(0.3)
    sys.exit("No RP2350 in BOOTSEL mode. Hold BOOT while plugging in USB and try again.")


PICOTOOL, BUILD_DIR = sys.argv[1], sys.argv[2]

if picotool("info", check=False).returncode != 0:
    print("Resetting the board into BOOTSEL...")
    picotool("reboot", "-f", "-u", check=False)
    time.sleep(1)
    wait_for_bootsel()

print("Writing partition table...")
picotool("load", os.path.join(BUILD_DIR, "pt.uf2"))
picotool("reboot", "-u")
time.sleep(1)
wait_for_bootsel()

print("Loading firmware...")
picotool("load", "-x", os.path.join(BUILD_DIR, "firmware.uf2"))
print("Done.")
