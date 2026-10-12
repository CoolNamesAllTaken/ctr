"""Firmware update over the network, including rollback and revert.

Slow (each test builds images and reflashes the board):

    uv run pytest tests/hil -m ota

Needs PlatformIO (~/.platformio/penv/bin/pio) to build test images with
LC_FW_VERSION, and the board's USB connected for the power-cycle steps
(picotool reboots it through BOOTSEL, which boots the ROM's normal path).
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from lcctl import image as fwimage
from lcctl import ota
from lcctl import regmap as rm
from lcctl.client import Board

pytestmark = pytest.mark.ota

PROJECT = Path(__file__).resolve().parents[3]
PIO = Path.home() / ".platformio/penv/bin/pio"
PICOTOOL = Path.home() / ".platformio/packages/tool-picotool-rp2040-earlephilhower/picotool"
TRIAL_TIMEOUT_S = 60   # src/boot_ctl.c


@pytest.fixture(scope="session")
def build(tmp_path_factory):
    """build(version, never_buy=False) -> Image, cached per session.

    never_buy builds an image that never leaves its trial (OTA_TEST_NEVER_BUY),
    to exercise rollback.
    """
    if not PIO.exists():
        pytest.skip("PlatformIO not installed")
    out = tmp_path_factory.mktemp("images")
    cache = {}

    def _build(version: int, never_buy: bool = False) -> fwimage.Image:
        key = (version, never_buy)
        if key not in cache:
            ver = rm.version_str(version)
            env = {**os.environ, "LC_FW_VERSION": ver}
            if never_buy:
                env["PLATFORMIO_BUILD_FLAGS"] = "-D OTA_TEST_NEVER_BUY=1"
            subprocess.run([str(PIO), "run", "-s", "-e", "app"], cwd=PROJECT, check=True, env=env,
                           stdout=subprocess.DEVNULL)
            dst = out / f"app-{ver}{'-nobuy' if never_buy else ''}.bin"
            shutil.copy(PROJECT / ".pio/build/app/firmware.bin", dst)
            cache[key] = fwimage.load(dst)
            assert cache[key].version == version
        return cache[key]

    return _build


def bump(v: int, n: int) -> int:
    """Version n patch steps away from v (0x00MMmmpp)."""
    major, rest = v >> 16, (v & 0xFFFF) + n
    return (major << 16) | rest


def state(ip: str) -> dict:
    b = ota.wait_for_board(ip)
    try:
        return b.info()
    finally:
        b.close()


def running(ip: str) -> int:
    return rm.parse_version(state(ip)["fw_version"])


def usb_power_cycle(ip: str) -> dict:
    """Reset through BOOTSEL; the ROM then boots normally, as after power-on."""
    if not PICOTOOL.exists():
        pytest.skip("picotool not available")
    for attempt in range(10):   # the app's USB may still be enumerating after a reboot
        r = subprocess.run([str(PICOTOOL), "reboot", "-f", "-u"], capture_output=True)
        if r.returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f"picotool could not reach the board: {r.stdout.decode()}{r.stderr.decode()}")
    time.sleep(2)
    subprocess.run([str(PICOTOOL), "reboot"], check=True, capture_output=True)
    time.sleep(1)
    return state(ip)


def start_trial(ip: str, img: fwimage.Image) -> None:
    """Send, verify and activate without reconnecting (so nothing buys yet)."""
    with Board(ip) as b:
        ota.begin(b, img)
        ota.send(b, img.data)
        ota.verify(b)
        ota.activate(b)
    time.sleep(1.0)


def quiet(msg: str) -> None:
    pass


# ---- tests ---------------------------------------------------------------------


def test_update_and_power_cycle(board_ip, build):
    before = state(board_ip)
    img = build(bump(running(board_ip), 1))
    after = ota.update(board_ip, img, log=quiet)
    assert after["fw_version"] == img.version_str and not after["trial"]
    assert after["boot_partition"] != before["boot_partition"]
    for _ in range(2):
        s = usb_power_cycle(board_ip)
        assert (s["fw_version"], s["boot_partition"], s["boot_type"]) == (img.version_str, after["boot_partition"], "normal")


def test_equal_version_rejected(board_ip, build):
    img = build(running(board_ip))
    with pytest.raises(ota.UpdateError):
        ota.check_version(img.version, img, allow_downgrade=True)
    with Board(board_ip) as b:
        with pytest.raises(ota.UpdateError):
            ota.begin(b, img)               # the board refuses it too
        assert ota.ota_status(b)["error"] == "same version"


def test_downgrade_needs_flag(board_ip, build):
    img = build(bump(running(board_ip), -1))
    with pytest.raises(ota.UpdateError, match="allow_downgrade"):
        ota.update(board_ip, img, log=quiet)


def test_corrupt_image_rejected(board_ip, build):
    before = state(board_ip)
    img = build(bump(running(board_ip), 1))
    bad = bytearray(img.data)
    bad[len(bad) // 2] ^= 0xFF
    with Board(board_ip) as b:
        ota.begin(b, img)                   # announces the good image's hash
        ota.send(b, bytes(bad))
        with pytest.raises(ota.UpdateError, match="hash mismatch"):
            ota.verify(b)
        with pytest.raises(ota.UpdateError):
            ota.activate(b)
        ota.abort(b)
    after = state(board_ip)
    assert (after["fw_version"], after["boot_partition"]) == (before["fw_version"], before["boot_partition"])


def test_interrupted_transfer_then_restart(board_ip, build):
    img = build(bump(running(board_ip), 1))
    with Board(board_ip) as b:
        ota.begin(b, img)
        ota.send(b, img.data, stop_at=len(img.data) // 2)
    # Connection dropped halfway; a fresh BEGIN starts over
    after = ota.update(board_ip, img, log=quiet)
    assert after["fw_version"] == img.version_str


def test_reset_during_trial_rolls_back(board_ip, build):
    before = state(board_ip)
    img = build(bump(running(board_ip), 1))
    start_trial(board_ip, img)              # no request reaches the trial image...
    s = usb_power_cycle(board_ip)           # ...before it is reset
    assert (s["fw_version"], s["boot_partition"]) == (before["fw_version"], before["boot_partition"])


def test_reboot_command_during_trial_rolls_back(board_ip, build):
    before = state(board_ip)
    img = build(bump(running(board_ip), 1), never_buy=True)
    start_trial(board_ip, img)
    with ota.wait_for_board(board_ip) as b:
        s = b.info()
        assert s["trial"] and s["fw_version"] == img.version_str
        with pytest.raises(ota.UpdateError, match="on trial"):
            ota.begin(b, build(bump(rm.parse_version(s["fw_version"]), 1)))   # no updates on top of a trial image
        b.reboot()
    time.sleep(1)
    s = state(board_ip)
    assert (s["fw_version"], s["boot_partition"], s["trial"]) == (before["fw_version"], before["boot_partition"], False)


def test_trial_deadline_rolls_back(board_ip, build):
    before = state(board_ip)
    img = build(bump(running(board_ip), 1), never_buy=True)
    start_trial(board_ip, img)
    assert state(board_ip)["trial"]
    deadline = time.monotonic() + TRIAL_TIMEOUT_S + 30
    while time.monotonic() < deadline:
        time.sleep(5)
        s = state(board_ip)
        if s["fw_version"] == before["fw_version"]:
            break
    assert (s["fw_version"], s["boot_partition"], s["reset_reason"]) == (before["fw_version"], before["boot_partition"], "requested")


def test_revert_then_upgrade(board_ip, build):
    current = running(board_ip)
    old = build(bump(current, -1))
    s = ota.update(board_ip, old, allow_downgrade=True, log=quiet)
    assert s["fw_version"] == old.version_str
    for _ in range(2):
        # The newer image in the other partition must not win on a normal boot
        assert usb_power_cycle(board_ip)["fw_version"] == old.version_str
    new = build(bump(current, 1))
    assert ota.update(board_ip, new, log=quiet)["fw_version"] == new.version_str


def test_unbought_revert_rolls_forward(board_ip, build):
    before = state(board_ip)
    old = build(bump(running(board_ip), -1), never_buy=True)
    start_trial(board_ip, old)
    with ota.wait_for_board(board_ip) as b:
        assert b.info()["trial"]
        b.reboot()
    time.sleep(1)
    s = state(board_ip)
    assert (s["fw_version"], s["boot_partition"]) == (before["fw_version"], before["boot_partition"])
