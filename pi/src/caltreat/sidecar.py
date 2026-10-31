"""The v12 sound engine (pi/engine/, node) as a supervised child process, and the engine object the app drives.

`Sidecar` starts `node pi/engine/sidecar.js`, restarts it if it dies (re-sending the settings and the train's state),
writes control messages to it (JSON lines, the same messages the v12 page sends) and reads, per block of BLOCK frames,
one sample per speaker bus (FRONT, INSIDE, REAR: each its own v12 mix). Engine state (speed, doors, ...) comes back
~20 times a second on a separate pipe.

`V12Engine` has the methods and properties the app used on the old numpy `AudioOut` (hold, set_bell, door_press,
stop_train, play_seq, ..., horn_on, doors, motor.speed), implemented as v12 messages, plus `render_buses()` for the
Router. The app falls back to `AudioOut` (the old sounds, flagged on the pages) when node is missing.
"""

import fcntl
import json
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

BLOCK = 512
BUSES = ("front", "inside", "rear")
ENGINE_DIR = Path(__file__).resolve().parents[2] / "engine"  # pi/engine
NODE_MIN = 18
# v12 page defaults (caltrain-sounds app/index.src.html, Mix & settings): % for each intensity key; master is sent as
# 1.1 * master / 100 and the rest as v / 100
MIX_DEFAULTS = {"master": 85, "horn": 85, "breath": 50, "bell": 75, "motor": 80, "door": 70, "doorMech": 70,
                "doorLeak": 70, "sand": 70, "hiss": 70, "interior": 0}
LAYERS = ("hvac", "traction", "rolling", "rumble", "clatter", "whine")


def find_node():
    """(path, major version) of a usable node, or (None, reason)."""
    cands = [os.environ.get("CALTREAT_NODE"), str(Path.home() / "caltreat" / "node" / "bin" / "node"),
             str(ENGINE_DIR.parent / "node" / "bin" / "node"), shutil.which("node"), shutil.which("nodejs")]
    for c in cands:
        if not c or not os.path.exists(c):
            continue
        try:
            v = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        m = re.match(r"v(\d+)", v)
        if m and int(m.group(1)) >= NODE_MIN:
            return c, int(m.group(1))
    return None, f"node {NODE_MIN}+ not found"


class Sidecar:
    RESTART = 2.0  # s between restarts
    READ_TIMEOUT = 0.03  # s a block may be late before the Router plays silence for it

    def __init__(self, node=None, warmup=True):
        self.node = node or find_node()[0]
        self.warmup = warmup
        self.proc = None
        self.ready = False
        self.state = {}
        self.error = None
        self.warm_ms = None
        self.started = 0.0
        self.restarts = 0
        self.late = 0  # blocks not ready in time
        self.replay = {}  # key -> message, sent again to a restarted engine
        self.wlock = threading.Lock()
        self.running = False
        self.partial = b""

    # ---------- process ----------
    def start(self):
        if not self.node:
            self.error = "node not found"
            return
        self.running = True
        threading.Thread(target=self._supervise, name="caltreat-sidecar", daemon=True).start()

    def stop(self):
        self.running = False
        p = self.proc
        if p:
            try:
                p.stdin.close()
            except OSError:
                pass
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()

    def _spawn(self):
        r, w = os.pipe()
        env = dict(os.environ, CALTREAT_STATE_FD=str(w))
        if not self.warmup:
            env["CALTREAT_ENGINE_WARMUP"] = "0"
        p = subprocess.Popen([self.node, str(ENGINE_DIR / "sidecar.js")], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, pass_fds=(w,), env=env, bufsize=0, cwd=str(ENGINE_DIR))
        os.close(w)
        try:  # keep little audio in flight: about one block of the three buses
            fcntl.fcntl(p.stdout.fileno(), fcntl.F_SETPIPE_SZ, 8192)
        except OSError:
            pass
        return p, os.fdopen(r, "r")

    def _supervise(self):
        while self.running:
            self.ready, self.partial = False, b""
            try:
                p, st = self._spawn()
            except OSError as e:
                self.error = f"engine: {e}"
                time.sleep(self.RESTART)
                continue
            self.proc, self.started = p, time.monotonic()
            with self.wlock:
                for m in self.replay.values():
                    self._write(m)
            for line in st:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("ready"):
                    self.ready, self.error, self.warm_ms = True, None, m.get("warmMs")
                    print(f"caltreat: v12 engine ready (warm-up {self.warm_ms} ms)", file=sys.stderr)
                elif "error" in m:
                    self.error = m["error"][:300]
                    print(f"caltreat: engine: {self.error}", file=sys.stderr)
                else:
                    self.state = m
            st.close()
            self.ready = False
            code = p.wait()
            if self.running:
                self.restarts += 1
                self.error = f"engine exited ({code}), restarting"
                print(f"caltreat: {self.error}", file=sys.stderr)
                time.sleep(self.RESTART)

    # ---------- control ----------
    def _write(self, m):
        p = self.proc
        if p is None or p.poll() is not None:
            return
        try:
            p.stdin.write((json.dumps(m, separators=(",", ":")) + "\n").encode())
        except (BrokenPipeError, OSError):
            pass

    def send(self, m, key=None):
        """Send a message; with a key it is also re-sent after an engine restart (latest per key)."""
        with self.wlock:
            if key:
                self.replay[key] = m
            self._write(m)

    # ---------- audio (the Router's thread) ----------
    def read_block(self):
        """(BLOCK, 3) float32 [front, inside, rear], or None if the engine has nothing ready in time."""
        p = self.proc
        if not self.ready or p is None:
            return None
        fd, need = p.stdout.fileno(), BLOCK * 3 * 4
        end = time.monotonic() + self.READ_TIMEOUT
        while len(self.partial) < need:
            left = end - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                self.late += 1
                return None  # keep the partial block for next time
            try:
                chunk = os.read(fd, need - len(self.partial))
            except OSError:
                return None
            if not chunk:
                return None
            self.partial += chunk
        data, self.partial = self.partial[:need], self.partial[need:]
        return np.frombuffer(data, dtype="<f4").reshape(BLOCK, 3)


class V12Engine:
    """The train for the app: the v12 engine's messages behind the old AudioOut's methods."""

    def __init__(self, sidecar):
        self.sc = sidecar
        self.lock = threading.Lock()
        self.holders = set()
        self.motor = SimpleNamespace(speed=0.0, throttle=0.0)
        self.bell = SimpleNamespace(on=False)
        self.pattern_key = None
        self.pattern_until = 0.0
        self.timing = "costume"
        self.t = 0  # output sample clock (blocks handed to the Router)
        self.silent = np.zeros((BLOCK, 3), dtype=np.float32)

    def send(self, m, key=None):
        self.sc.send(m, key)

    # ---------- controls ----------
    def hold(self, who, on):
        with self.lock:
            before = bool(self.holders)
            (self.holders.add if on else self.holders.discard)(who)
            now = bool(self.holders)
        if now and not before:
            self.pattern_until = 0.0
            self.send({"t": "stopPattern"})
            self.send({"t": "horn", "on": True})
        elif before and not now:
            self.send({"t": "horn", "on": False})

    def set_bell(self, on):
        self.bell.on = bool(on)
        self.send({"t": "bell", "on": self.bell.on}, "bell")

    def toggle_bell(self):
        self.set_bell(not self.bell.on)

    def door_press(self, who):
        """The DOORS pad (v12): tap toggles; a tap while they move reverses them."""
        self.send({"t": "doors", "v": "toggle"})

    def door_release(self, who):
        pass

    def doors_cmd(self, v):
        self.send({"t": "doors", "v": v})

    def air(self):
        self.send({"t": "hiss"})

    def stop_train(self):
        """STOP (v12): brake to a halt (squeal, thump, air, the sander as set); standing: the last seconds of one."""
        self.send({"t": "stop"})
        self.set_throttle(0.0)

    def set_throttle(self, v):
        self.motor.throttle = min(max(float(v), 0.0), 1.0)
        self.send({"t": "throttle", "v": self.motor.throttle}, "throttle")

    def play_seq(self, seq, key=None):
        seq = [[float(s), float(d)] for s, d in seq]
        self.pattern_key = key
        self.pattern_until = time.monotonic() + (seq[-1][0] + seq[-1][1] if seq else 0)
        self.send({"t": "pattern", "seq": seq})

    def stop_pattern(self):
        self.pattern_until = 0.0
        self.send({"t": "stopPattern"})

    def set_timing(self, mode):
        self.timing = "real" if mode == "real" else "costume"
        self.send({"t": "accel", "v": 60 if self.timing == "real" else 14}, "accel")

    def apply_settings(self, c):
        """The v12 settings from the config: mix, layers, sander, door sound, outside environments."""
        mix = {**MIX_DEFAULTS, **c.get("mix12", {})}
        self.send({"t": "master", "v": 1.1 * mix["master"] / 100}, "master")
        for k, v in mix.items():
            if k != "master":
                self.send({"t": "intensity", "k": k, "v": v / 100}, "intensity:" + k)
        for k, lay in c.get("layers", {}).items():
            self.send({"t": "layer", "k": k, "mode": lay.get("mode", "auto"), "v": lay.get("v", 100) / 100},
                      "layer:" + k)
        self.send({"t": "autoSand", "v": c.get("auto_sand", "inside")}, "autoSand")
        self.send({"t": "doorSound", "v": c.get("door_sound", "recorded")}, "doorSound")
        for bus, env in c.get("env", {}).items():
            self.send({"t": "busEnv", "bus": bus, "env": env}, "busEnv:" + bus)

    def load_doors(self, folder, name="doors_v10"):
        self.send({"t": "doorFiles", "dir": str(folder), "name": name}, "doorFiles")

    def apply_mix(self, mix):  # the old engine's per-sound levels: not used by v12 (see apply_settings)
        pass

    def release(self, who):
        self.hold(who, False)

    # ---------- state ----------
    def _st(self):
        return self.sc.state

    @property
    def horn_on(self):
        return bool(self.holders) or bool(self._st().get("horn"))

    @property
    def busy_pattern(self):
        busy = self._st().get("patternBusy") or time.monotonic() < self.pattern_until
        return self.pattern_key if busy else None

    @property
    def doors(self):
        return self._st().get("doors", "closed")

    @property
    def door_pos(self):
        return float(self._st().get("doorPos", 0.0))

    @property
    def inverter_hz(self):
        return float(self._st().get("A", 0.0))

    @property
    def door_sound(self):
        return self._st().get("doorSound")

    def state(self):
        return {"speed": self.motor.speed, "doors": self.doors}

    # ---------- audio ----------
    def render_buses(self):
        """{bus: (BLOCK,) float} for the Router (silence while the engine starts or is late)."""
        b = self.sc.read_block()
        self.motor.speed = float(self._st().get("speed", 0.0))
        self.t += BLOCK
        b = self.silent if b is None else b
        return {"front": b[:, 0], "inside": b[:, 1], "rear": b[:, 2]}
