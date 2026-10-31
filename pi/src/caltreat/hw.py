"""The Pi's sound hardware: ALSA players (`aplay`), USB sound cards, mixer volumes.

Without the Pi's tools (no `aplay`, e.g. on a laptop) or with CALTREAT_FAKE_AUDIO set, players are a small Python sink
(`caltreat.fakesink`) that consumes audio at real time like a sound card. If CALTREAT_FAKE_AUDIO is a directory, every
player writes its raw PCM there (`<device>-<n>.raw` plus a `.json` with its start time), for tests. Fake mode also
reports two fake USB cards (ports 1.2 and 1.3), so the settings page can be tried out.
"""

import os
import re
import shutil
import subprocess
import sys

SR = 48000
JACK = "jack"  # the 3.5 mm jack (ALSA card Headphones)
JACK_CARD = "Headphones"
JACK_MIXER = "PCM"
USB_MIXER = "Speaker"  # the C-Media USB cards' playback volume
# ALSA buffer per card: what sounds play late by (with ~30 ms of queue and pipe on top). Raise it if the log shows
# underruns ("aplay ... underrun"); the intercom aims at < 150 ms from the phone's microphone.
BUFFER_MS = int(os.environ.get("CALTREAT_ALSA_BUFFER_MS", "40"))

FAKE = os.environ.get("CALTREAT_FAKE_AUDIO") or (None if shutil.which("aplay") else "1")
FAKE_DIR = FAKE if FAKE and os.path.isdir(FAKE) else None
FAKE_CARDS = [("Device", "1.2"), ("Device_1", "1.3")]


def usb_outputs():
    """[(ALSA card id, USB port path)] for the USB sound cards, ordered by port. Card ids (Device, Device_1, ...)
    follow enumeration order and can swap between boots; the port path is fixed by where a card is plugged in."""
    if FAKE:
        return list(FAKE_CARDS)
    try:
        cards = open("/proc/asound/cards").read()
    except OSError:
        return []
    found = re.findall(r"^\s*\d+ \[(\S+?)\s*\]: USB-Audio - .*\n\s*.* at usb-[^-]+-([\d.]+)", cards, re.M)
    return sorted(found, key=lambda c: [int(n) for n in c[1].split(".")])


def resolve(device, usb=None):
    """A configured device ("jack", "usb:<port>", "usb#<n>" = n-th USB card by port, "none") -> (ALSA card id or
    None, description). usb: usb_outputs() if already known."""
    usb = usb_outputs() if usb is None else usb
    if device == JACK:
        return JACK_CARD, "3.5 mm jack"
    if device.startswith("usb#"):
        n = int(device[4:])
        if 0 < n <= len(usb):
            return usb[n - 1][0], f"USB #{n} (port {usb[n - 1][1]})"
        return None, f"USB #{n}"
    if device.startswith("usb:"):
        port = device[4:]
        for card, p in usb:
            if p == port:
                return card, f"USB port {port}"
        return None, f"USB port {port}"
    return None, "none"


def player(card, channels=2, name=None):
    """A Popen taking raw S16_LE frames at SR on stdin, playing on the ALSA card (or the fake sink)."""
    if FAKE:
        return subprocess.Popen([sys.executable, "-m", "caltreat.fakesink", FAKE_DIR or "-", name or card,
                                 str(SR), str(channels), str(BUFFER_MS)], stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                bufsize=0)
    return subprocess.Popen(
        ["aplay", "-q", "-D", f"plughw:CARD={card}", "-t", "raw", "-f", "S16_LE", "-r", str(SR), "-c", str(channels),
         f"--buffer-time={BUFFER_MS * 1000}", f"--period-time={BUFFER_MS * 250}"],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)  # unbuffered: a buffered stdin holds ~40 ms


_fake_volumes = {}


def get_volume(card=JACK_CARD, control=JACK_MIXER):
    if FAKE:
        return _fake_volumes.get(card, 80)
    try:
        out = subprocess.run(["amixer", "-c", card, "-M", "sget", control], capture_output=True, text=True).stdout
    except OSError:
        return 50
    m = re.search(r"\[(\d+)%\]", out)
    return int(m.group(1)) if m else 50


def set_volume(pct, card=JACK_CARD, control=JACK_MIXER):
    if FAKE:
        _fake_volumes[card] = int(pct)
        return
    try:
        subprocess.run(["amixer", "-c", card, "-M", "-q", "sset", control, f"{int(pct)}%"])
    except OSError:
        pass


def mixer_control(card):
    return JACK_MIXER if card == JACK_CARD else USB_MIXER
