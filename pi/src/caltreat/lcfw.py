"""Lighting controller firmware from the app: each board's version (read by its `RemoteBoard` when it connects) and
updates over the network with lcctl's update steps (caltreat.lcctl, vendored from the firmware's tools).

A board serves one Modbus client at a time, so an update takes the board away from the app: its `RemoteBoard` stops
(the board's lights go to their failsafe defaults, the engineer page shows it as unavailable) and `BoardFinder` leaves
its address alone. The image is sent, checked by the board (SHA-256) and started; the board reboots into it on trial
and keeps it once we reach it again, else its boot ROM goes back to the old image. Then the `RemoteBoard` starts
again and sets the outputs up as mapped.

    info = await lcfw.update(finder, board, lcfw.bundled())   # raises UpdateError
"""

from __future__ import annotations

import asyncio
import copy
import os
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .lcctl import image as fwimage
from .lcctl import ota
from .lcctl import regmap as rm
from .lcctl.client import Board, parse_info
from .lcctl.image import Image
from .lcctl.ota import UpdateError

# Put there by `deploy.sh --with-lc-firmware` (gitignored), so updates work without internet
BUNDLED = Path(os.environ.get("CALTREAT_LC_FIRMWARE") or
               Path(__file__).resolve().parents[2] / "lc-firmware" / "lighting-controller.bin")
CONNECT_TIMEOUT = 10.0  # s to get the board after the app let go of it
BOOT_TIMEOUT = 40.0  # s for the board to come back after starting the new image
BUY_TIMEOUT = 10.0  # s for the new image to leave its trial once we reach it
PAUSED = "⟳ firmware update"  # RemoteBoard.error while paused (shown on the engineer page)
REGMAP_VERSION = rm.REGMAP_VERSION


def read_info(conn):
    """The board's info (see lcctl.client.parse_info) over any connection with read_input(address, count), e.g. the
    app's ModbusTCP; None if it can't be read (old firmware)."""
    try:
        return parse_info(conn.read_input(rm.IR_MAP_VERSION, rm.IR_INFO_COUNT))
    except (OSError, ValueError, IndexError, struct.error):
        return None


_bundled = {}


def bundled():
    """The image deployed with the app, or None."""
    try:
        st = BUNDLED.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    if _bundled.get("key") != key:
        try:
            img = fwimage.load(BUNDLED)
        except (OSError, ValueError):
            img = None
        _bundled.update(key=key, image=img)
    return _bundled["image"]


def load(data: bytes, name="upload.bin") -> Image:
    """An image from bytes (an upload). ValueError if it isn't a lighting controller image."""
    with tempfile.TemporaryDirectory(prefix="caltreat-lcfw-") as d:
        p = Path(d) / Path(name).name
        p.write_bytes(data)
        return fwimage.load(p)


@dataclass
class Job:
    """One board's update, shared by every browser (`jobs`). stage: check, connect, send, verify, reboot, done,
    failed. Updated from the update's thread."""
    uid: str
    target: str
    stage: str = "check"
    sent: int = 0
    total: int = 0
    message: str = ""
    started: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def running(self):
        return self.stage not in ("done", "failed")

    @property
    def fraction(self):
        if self.stage in ("verify", "reboot", "done"):
            return 1.0
        return self.sent / self.total if self.total else 0.0


jobs: dict[str, Job] = {}  # chip ID -> latest update


def check(info, image: Image, allow_downgrade=False):
    """UpdateError if `image` must not go onto a board running `info`."""
    if info.get("trial"):
        raise UpdateError("the running image is still on trial")
    ota.check_version(info["fw"], image, allow_downgrade)


def cancel(uid):
    """Stop an update that is still sending (after that the board finishes on its own)."""
    job = jobs.get(uid)
    if job and job.stage in ("check", "connect", "send"):
        job.cancel.set()


def pause(finder, board):
    """Take the board away from the app: stop its RemoteBoard (closing its connection) and keep BoardFinder off its
    address."""
    board.paused = True  # BoardFinder.scan skips it
    board.stop()
    deadline = time.monotonic() + 10
    while board.thread and board.thread.is_alive() and time.monotonic() < deadline:
        board.thread.join(0.2)
    board.error = PAUSED


def resume(finder, board):
    """Give the board back to the app: its RemoteBoard reconnects and sets the outputs up as mapped."""
    board.error = None
    board.paused = False
    board.start()
    finder.wake.set()


def _connect(board, deadline):
    """A connection to `board` (by chip ID; BoardFinder may move board.host while we wait)."""
    err = None
    while time.monotonic() < deadline:
        b = Board(board.host, timeout=1.0)
        try:
            b.connect()
            info = b.info()
            if info["uid"] == board.uid:
                return b, info
            err = UpdateError(f"{board.host} is board {info['uid']} now")
        except OSError as e:
            err = e
        b.close()
        time.sleep(0.3)
    raise UpdateError(f"no connection to the board: {err}")


def _run(finder, board, image, allow_downgrade, job, boot_timeout, notify):
    def stage(name, **kw):
        job.stage = name
        for k, v in kw.items():
            setattr(job, k, v)
        notify()

    if getattr(board, "fw", None):
        check(board.fw, image, allow_downgrade)  # refuse before taking the board away
    stage("connect")
    pause(finder, board)
    try:
        b, info = _connect(board, time.monotonic() + CONNECT_TIMEOUT)
        try:
            board.fw = info
            check(info, image, allow_downgrade)
            stage("send", sent=0, total=len(image.data))
            ota.begin(b, image)

            def progress(sent, total):
                if job.cancel.is_set():
                    raise UpdateError("cancelled")
                job.sent = sent
                notify()

            ota.send(b, image.data, progress)
            stage("verify")
            ota.verify(b)
            if job.cancel.is_set():
                raise UpdateError("cancelled")
            stage("reboot")
            ota.activate(b)  # the board restarts into the new image
        except BaseException:
            try:
                ota.abort(b)  # nothing was started; drop what the board received
            except Exception:
                pass
            b.close()
            raise
        time.sleep(1.0)
        b, info = _connect(board, time.monotonic() + boot_timeout)
        try:
            # our requests make the new image keep itself
            deadline = time.monotonic() + BUY_TIMEOUT
            while info["trial"] and time.monotonic() < deadline:
                time.sleep(0.2)
                info = b.info()
        finally:
            b.close()
        board.fw = info
        if info["fw"] != image.version or info["trial"]:
            raise UpdateError(f"came back running {info['fw_version']}{' on trial' if info['trial'] else ''}; "
                              "rolled back")
        return info
    finally:
        resume(finder, board)


async def update(finder, board, image: Image, allow_downgrade=False, progress=None, boot_timeout=BOOT_TIMEOUT):
    """Update one board (a RemoteBoard of `finder`) to `image`; returns its info afterwards. progress(job) is called
    on the event loop with a copy of the `Job` as the update moves on. Raises UpdateError (refused, failed, cancelled, rolled back) or
    OSError; the board is given back to the app either way."""
    old = jobs.get(board.uid)
    if old and old.running:
        raise UpdateError("already updating")
    job = jobs[board.uid] = Job(board.uid, image.version_str)
    loop = asyncio.get_running_loop()

    def notify():
        if progress:
            loop.call_soon_threadsafe(progress, copy.copy(job))  # as it is now; the update moves on meanwhile

    try:
        info = await asyncio.to_thread(_run, finder, board, image, allow_downgrade, job, boot_timeout, notify)
    except (UpdateError, OSError) as e:
        job.stage, job.message = "failed", str(e) or type(e).__name__
        notify()
        raise
    job.stage, job.message = "done", f"{info['fw_version']} on partition {info['boot_partition']}"
    notify()
    return info


async def update_all(finder, image: Image, allow_downgrade=False, progress=None, boot_timeout=BOOT_TIMEOUT):
    """Update every found board that doesn't run `image`, one after the other. {chip ID: info or the error}."""
    out = {}
    for board in finder.found:
        fw = getattr(board, "fw", None)
        if fw and fw["fw"] == image.version and not fw["trial"]:
            continue
        try:
            out[board.uid] = await update(finder, board, image, allow_downgrade, progress, boot_timeout)
        except (UpdateError, OSError) as e:
            out[board.uid] = e
    return out
