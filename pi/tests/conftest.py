"""Test setup: no Pi hardware. Fake audio (players write raw PCM files into a temp folder), a temp config folder, and
lighting controller boards looked for on 127.0.1.x at a free port (see fake_board.py). Must run before caltreat is imported.

    cd pi && poetry run pip install pytest && poetry run pytest
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="caltreat-test-"))
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)  # only the folder made here
(_tmp / "audio").mkdir()
os.environ["CALTREAT_FAKE_AUDIO"] = str(_tmp / "audio")
os.environ["CALTREAT_CONFIG_DIR"] = str(_tmp / "config")
os.environ["CALTREAT_BOARD_NET"] = "127.0.1."
def _free_port():
    """A port free on the fake boards' addresses (other test runs on this computer may use a fixed one)."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.1.10", 0))
        port = s.getsockname()[1]
    for host in ("127.0.1.11", "127.0.1.12"):
        with socket.socket() as s:
            s.bind((host, port))
    return port


os.environ["CALTREAT_BOARD_PORT"] = str(_free_port())
os.environ["CALTREAT_HTTPS_PORT"] = "0"
os.environ["CALTREAT_HTTP_PORTS"] = ""  # no port 80 in tests
os.environ["CALTREAT_ENGINE_WARMUP"] = "0"  # the v12 engine's warm-up (~15 s here) only matters for a Pi's CPU
sys.path.insert(0, str(Path(__file__).parent))

TMP = _tmp


def pytest_sessionfinish(session, exitstatus):
    """Remove the temp folder once the tests are done (atexit is the fallback)."""
    shutil.rmtree(_tmp, ignore_errors=True)
