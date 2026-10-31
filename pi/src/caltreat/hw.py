"""The Pi's sound hardware: ALSA players (`aplay`), USB sound cards, mixer volumes.

Without the Pi's tools (no `aplay`, e.g. on a laptop) or with CALTREAT_FAKE_AUDIO set, players are a small Python sink
(`caltreat.fakesink`) that consumes audio at real time like a sound card. If CALTREAT_FAKE_AUDIO is a directory, every
player writes its raw PCM there (`<device>-<n>.raw` plus a `.json` with its start time), for tests. Fake mode also
reports two fake USB cards (ports 1.2 and 1.3), so the settings page can be tried out.
"""

import math
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
# ALSA buffer per card: what sounds play late by (with ~30 ms of queue and pipe on top), and how long the app may
# stall before a card runs dry. 60 ms (was 40): the cards ride out a short busy spell of the web side (the players
# wait up to 60% of it for a late block instead of playing silence). 80-100 ms rides out more, if the Health panel
# still shows underruns (CALTREAT_ALSA_BUFFER_MS in the service), but the intercom (which aims at < 150 ms from the
# phone's microphone) then runs ~160 ms late.
BUFFER_MS = int(os.environ.get("CALTREAT_ALSA_BUFFER_MS", "60"))

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


# Card volumes are set in dB (not amixer -M %, which lands on a different dB on each card). Each mixer control's dB
# range, when amixer can't tell (fake mode):
JACK_DB = (-102.39, 4.0)  # bcm2835 PCM: -10239..400 in 0.01 dB (kernel bcm2835-ctl.c)
USB_DB = (-28.37, -0.06)  # C-Media CM108/CM119 Speaker: 0..151 raw, about 0.19 dB a step
# Card default: full scale. The jack's PCM above 0 dB is digital gain (clips loud sounds), so 0 dB there.
DEFAULT_DB = 0.0
# Card trim: app gain (dB) per card so the same level sounds the same on any output. The Pi 4 jack's full scale is
# about 1 Vpp (~0.35 Vrms: 3.3 V PWM through its divider), a C-Media DAC's about 1 Vrms: the jack is ~9 dB quieter.
# An estimate: adjust by ear. Gain above full scale goes through the Router's soft limiter.
JACK_TRIM_DB = 9.0
USB_TRIM_DB = 0.0

_fake_db = {}


def default_trim(device):
    return JACK_TRIM_DB if device == JACK else USB_TRIM_DB


def db_range(card=JACK_CARD, control=JACK_MIXER):
    """(min, max) dB of the card's playback volume control."""
    dflt = JACK_DB if card == JACK_CARD else USB_DB
    if FAKE:
        return dflt
    try:
        out = subprocess.run(["amixer", "-c", card, "cget", f"name={control} Playback Volume"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return dflt
    if m := re.search(r"dBminmax(?:mute)?-min=(-?[\d.]+)dB,max=(-?[\d.]+)dB", out):
        return float(m[1]), float(m[2])
    raw = re.search(r"min=(-?\d+),max=(-?\d+)", out)
    if (m := re.search(r"dBscale-min=(-?[\d.]+)dB,step=([\d.]+)dB", out)) and raw:
        lo = float(m[1])
        return lo, round(lo + float(m[2]) * (int(raw[2]) - int(raw[1])), 2)
    return dflt


def get_volume_db(card=JACK_CARD, control=JACK_MIXER):
    """The control's current dB, or None if unknown."""
    if FAKE:
        return _fake_db.get(card)
    try:
        out = subprocess.run(["amixer", "-c", card, "sget", control], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"\[(-?[\d.]+)dB\]", out)
    return float(m[1]) if m else None


def set_volume_db(db, card=JACK_CARD, control=JACK_MIXER, rng=None):
    """Set the control to db (clamped to its range); the dB set."""
    lo, hi = rng or db_range(card, control)
    db = round(min(hi, max(lo, float(db))), 2)
    if FAKE:
        _fake_db[card] = db
        return db
    try:  # "--": a negative value isn't an option
        subprocess.run(["amixer", "-c", card, "-q", "--", "sset", control, f"{db:.2f}dB"], timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return db


def pct_to_db(pct, rng):
    """An `amixer -M` percentage -> the dB it set (alsa-utils volume_mapping.c), for migrating old settings."""
    lo, hi = rng
    v = min(1.0, max(0.0, pct / 100))
    if hi - lo <= 24:  # MAX_LINEAR_DB_SCALE: linear in dB
        return round(lo + v * (hi - lo), 2)
    mn = 10 ** ((lo - hi) / 60)
    v = v * (1 - mn) + mn
    return round(max(lo, 60 * math.log10(max(v, 1e-36)) + hi), 2)


def mixer_control(card):
    return JACK_MIXER if card == JACK_CARD else USB_MIXER
