"""The v12 engine sidecar (pi/engine): its per-bus PCM, read through the app's reader, equals the v12 page's Output
selector (KissBuses.Preview) for the same messages; the vendored engine files match caltrain-sounds."""

import json
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from caltreat.sidecar import BLOCK, ENGINE_DIR, Sidecar, find_node

NODE = find_node()[0]
pytestmark = pytest.mark.skipif(not NODE, reason="node 18+ not installed")
SR = 48000
T0 = 48 * BLOCK  # the score starts 0.5 s in (the sidecar renders a block ahead before any message)


def scene():
    """Horn holds, bell, doors open and closed, depart to ~50 mph, STOP to a halt."""
    s = [(0.0, {"t": "accel", "v": 14}), (0.5, {"t": "horn", "on": True}), (2.0, {"t": "horn", "on": False}),
         (2.5, {"t": "bell", "on": True}), (3.0, {"t": "doors", "v": "open"}), (8.0, {"t": "doors", "v": "close"}),
         (13.0, {"t": "hiss"}), (14.0, {"t": "pattern", "seq": [[0, 1.6], [2.1, 1.6]]}),
         (18.0, {"t": "throttle", "v": 0.63}), (22.0, {"t": "bell", "on": False}), (30.0, {"t": "horn", "on": True}),
         (31.0, {"t": "horn", "on": False}), (34.0, {"t": "stop"}), (34.0, {"t": "throttle", "v": 0})]
    return [(T0 + round(t * SR / BLOCK) * BLOCK, m) for t, m in s]


def reference(score, frames, tmp_path):
    f = tmp_path / "score.json"
    f.write_text(json.dumps(score))
    out = subprocess.run([NODE, str(Path(__file__).parent / "engine_reference.js"), str(f), str(frames)],
                         capture_output=True, check=True, timeout=600).stdout
    return np.frombuffer(out, dtype="<f4").reshape(-1, 3)


def test_sidecar_equals_the_v12_page_per_bus(tmp_path):
    score = scene()
    frames = T0 + 50 * SR // BLOCK * BLOCK
    sc = Sidecar(NODE, warmup=False)
    sc.start()
    end = time.monotonic() + 20
    while not sc.ready and time.monotonic() < end:
        time.sleep(0.02)
    assert sc.ready, sc.error
    for at, m in score:
        sc.send({**m, "at": at})
    sc.READ_TIMEOUT = 5.0
    blocks, states = [], []
    for _ in range(frames // BLOCK):
        b = sc.read_block()
        assert b is not None
        blocks.append(b.copy())
        states.append(dict(sc.state))
    sc.stop()
    got = np.concatenate(blocks)
    ref = reference(score, frames, tmp_path)
    for i, bus in enumerate(("front", "inside", "rear")):
        assert np.max(np.abs(ref[:, i])) > 0.05, bus
        assert np.array_equal(got[:, i], ref[:, i]), (bus, float(np.max(np.abs(got[:, i] - ref[:, i]))))
    # the state the app sees: the train moved, then STOP brought it to a halt; the doors moved
    speeds = [s.get("speed", 0) for s in states]
    assert max(speeds) * 79 > 40 and speeds[-1] < 0.003
    assert {"opening", "closing"} <= {s.get("doors") for s in states}


def test_vendored_engine_matches_caltrain_sounds():
    src = Path("/workspace/projects/caltrain-sounds")
    if not (src / "app" / "engine.js").exists() or not shutil.which("git"):
        pytest.skip("caltrain-sounds not on this computer")
    for name, rel in (("engine.js", "app/engine.js"), ("buses.js", "app/buses.js"), ("load.js", "app/samples/load.js")):
        ours = (ENGINE_DIR / name).read_text().split("\n", 1)
        assert ours[0].startswith("// vendored from caltrain-sounds " + rel), name
        rev = ours[0].split(" at ")[1].split()[0]
        theirs = subprocess.run(["git", "-C", str(src), "show", f"{rev}:{rel}"], capture_output=True, text=True,
                                check=True).stdout
        assert ours[1] == theirs, f"{name} differs from caltrain-sounds {rev}:{rel} (run pi/engine/sync.sh)"
