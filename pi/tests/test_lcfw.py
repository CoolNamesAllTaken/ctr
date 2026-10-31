"""Lighting controller firmware from the app (caltreat.lcfw): version reads and updates against a fake board with
the firmware's update registers (fake_ota_board.py), the app's own connection to the board released and given back,
and the vendored copy of lcctl matching firmware/caltreat_lighting_controller/tools/src/lcctl.

    python pi/tests/test_lcfw.py --sync   # copy lcctl's modules into pi/src/caltreat/lcctl again
"""

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

PI = Path(__file__).resolve().parents[1]
LCCTL = PI.parent / "firmware/caltreat_lighting_controller/tools/src/lcctl"
VENDORED = PI / "src/caltreat/lcctl"
MODULES = ("image.py", "regmap.py", "ota.py")
# The only change: FileRecord comes from the plain-socket client instead of pymodbus
SUBST = {"from pymodbus.pdu.file_message import FileRecord\n": "from .client import FileRecord\n"}


def vendored_text(name):
    """What pi/src/caltreat/lcctl/<name> must contain, from the firmware's lcctl."""
    text = (LCCTL / name).read_text()
    for a, b in SUBST.items():
        text = text.replace(a, b)
    return (f"# Vendored from firmware/caltreat_lighting_controller/tools/src/lcctl/{name} (deploy.sh copies only pi/).\n"
            "# Don't edit: change the original, then run `python pi/tests/test_lcfw.py --sync`.\n" + text)


if __name__ == "__main__" and "--sync" in sys.argv:
    for n in MODULES:
        (VENDORED / n).write_text(vendored_text(n))
        print(f"vendored {n}")
    sys.exit(0)

import pytest  # noqa: E402
from fake_ota_board import FakeOtaBoard, make_image  # noqa: E402

import caltreat.__main__ as m  # noqa: E402
from caltreat import lcfw  # noqa: E402

UID = "E6614103E7A1B2D4"
V060, V061, V059 = 0x000600, 0x000601, 0x000509


@pytest.mark.skipif(not LCCTL.is_dir(), reason="firmware/ not checked out")
@pytest.mark.parametrize("name", MODULES)
def test_vendored_lcctl_matches_firmware(name):
    assert (VENDORED / name).read_text() == vendored_text(name), \
        f"pi/src/caltreat/lcctl/{name} differs from {LCCTL / name}: run python pi/tests/test_lcfw.py --sync"



def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def rig(monkeypatch):
    """A fake OTA board found by its own BoardFinder, with GP3 mapped to the interior lights (on). On its own
    loopback net, away from test_app's board (and from other checkouts' tests running at the same time)."""
    monkeypatch.setattr(m, "REMOTE_NET", "127.0.7.")
    board = FakeOtaBoard("127.0.7.11", int(os.environ["CALTREAT_BOARD_PORT"]), uid=UID, fw=V060).start()
    finder = m.BoardFinder(m.cfg)
    io = m.Io(m.audio, m.cfg, finder)
    finder.start()
    io.start()
    assert wait_for(lambda: any(b.uid == UID and b.connected and getattr(b, "fw", None) for b in finder.found), 15)
    rb = next(b for b in finder.found if b.uid == UID)
    m.cfg.data["boards"][UID]["outputs"][3] = {"fn": "interior", "alt": False}
    io.set_mode("interior", "on")
    assert wait_for(lambda: board.coils[3])
    lcfw.jobs.clear()
    yield board, finder, rb
    io.set_mode("interior", "off")
    io.stop()
    finder.stop()
    board.stop()


def image(version):
    return lcfw.load(make_image(version))


def given_back(board, rb):
    """The app has the board again and its outputs are set up as mapped."""
    return wait_for(lambda: rb.connected and not rb.error and board.coils[3] and not board.failsafe, 10)


def test_version_read_when_connected(rig):
    board, finder, rb = rig
    assert rb.fw["fw_version"] == "0.6.0" and rb.fw["regmap"] == lcfw.REGMAP_VERSION == 1
    assert rb.fw["uid"] == UID and rb.fw["mac"] == "28:CD:C1:0A:0B:0D" and not rb.fw["trial"]


def test_update_releases_and_restores_the_app_connection(rig):
    board, finder, rb = rig
    seen = []

    def progress(job):  # on the event loop, while the update runs
        seen.append((job.stage, job.sent, rb.connected, rb.error, board.clients))

    info = asyncio.run(lcfw.update(finder, rb, image(V061), progress=progress))
    assert info["fw_version"] == "0.6.1" and not info["trial"] and board.fw == V061 and board.partition == 1
    assert [e for _, e in board.log].count("buy") == 1  # our reconnect made the new image keep itself
    sends = [s for s in seen if s[0] == "send"]
    assert sends and sends[-1][1] == 20000
    assert all(not c and e == lcfw.PAUSED and n <= 1 for _, _, c, e, n in sends)  # the app had let go
    assert not [r for r in board.refused if r[1] == 1]  # nobody knocked while receiving
    assert [s[0] for s in seen][-3:] == ["verify", "reboot", "done"]
    assert lcfw.jobs[UID].stage == "done"
    assert given_back(board, rb)
    assert wait_for(lambda: rb.fw["fw_version"] == "0.6.1")  # re-read by the app's own connection


def test_verify_failure_aborts_and_keeps_the_old_image(rig):
    board, finder, rb = rig
    board.corrupt = True
    with pytest.raises(lcfw.UpdateError, match="hash mismatch"):
        asyncio.run(lcfw.update(finder, rb, image(V061)))
    events = [e for _, e in board.log]
    assert "abort" in events and "activate" not in events and board.fw == V060
    assert lcfw.jobs[UID].stage == "failed" and "hash mismatch" in lcfw.jobs[UID].message
    assert given_back(board, rb)


def test_board_not_coming_back(rig):
    board, finder, rb = rig
    board.never_return = True
    t0 = time.monotonic()
    with pytest.raises(lcfw.UpdateError, match="no connection"):
        asyncio.run(lcfw.update(finder, rb, image(V061), boot_timeout=2))
    assert time.monotonic() - t0 < 10
    assert not rb.paused and rb.running and lcfw.jobs[UID].stage == "failed"  # given back, retrying
    board.offline_until = 0  # it shows up after all
    assert given_back(board, rb)
    assert wait_for(lambda: rb.fw["fw_version"] == "0.6.1" and not rb.fw["trial"])


def test_rolled_back_image_reported(rig):
    board, finder, rb = rig
    board.rollback = True
    with pytest.raises(lcfw.UpdateError, match="rolled back"):
        asyncio.run(lcfw.update(finder, rb, image(V061)))
    assert board.fw == V060 and given_back(board, rb)


def test_downgrade_refused_without_taking_the_board(rig):
    board, finder, rb = rig
    connects = [e for _, e in board.log].count("connect")
    with pytest.raises(lcfw.UpdateError, match="allow_downgrade"):
        asyncio.run(lcfw.update(finder, rb, image(V059)))
    with pytest.raises(lcfw.UpdateError, match="already runs"):
        asyncio.run(lcfw.update(finder, rb, image(V060), allow_downgrade=True))
    assert [e for _, e in board.log].count("connect") == connects and rb.connected  # never let go of it
    assert "begin" not in [e for _, e in board.log]
    info = asyncio.run(lcfw.update(finder, rb, image(V059), allow_downgrade=True))
    assert info["fw_version"] == "0.5.9" and given_back(board, rb)


def test_cancel_while_sending(rig):
    board, finder, rb = rig
    board.delay_s = 0.02
    result = {}

    def run():
        try:
            asyncio.run(lcfw.update(finder, rb, image(V061)))
        except lcfw.UpdateError as e:
            result["e"] = e

    t = threading.Thread(target=run)
    t.start()
    assert wait_for(lambda: UID in lcfw.jobs and lcfw.jobs[UID].sent > 2000)
    lcfw.cancel(UID)
    t.join(10)
    assert "cancelled" in str(result.get("e"))
    assert "abort" in [e for _, e in board.log] and board.fw == V060 and board.ota["state"] == 0
    assert given_back(board, rb)


def test_update_all_skips_boards_on_the_image(rig):
    board, finder, rb = rig
    assert asyncio.run(lcfw.update_all(finder, image(V060))) == {}
    out = asyncio.run(lcfw.update_all(finder, image(V061)))
    assert out[UID]["fw_version"] == "0.6.1"


def test_bundled_image(tmp_path, monkeypatch):
    assert lcfw.bundled() is None or lcfw.BUNDLED.exists()
    p = tmp_path / "lighting-controller.bin"
    p.write_bytes(make_image(V061))
    monkeypatch.setattr(lcfw, "BUNDLED", p)
    assert lcfw.bundled().version_str == "0.6.1"
    p.write_bytes(b"not firmware")
    assert lcfw.bundled() is None
    with pytest.raises(ValueError, match="not a lighting controller image"):
        lcfw.load(b"x" * 100)
