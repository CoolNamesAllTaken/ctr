"""Test setup: no Pi hardware. Fake audio (players write raw PCM files into a temp folder), a temp config folder, and
lighting controller boards looked for on 127.0.1.x:15021 (see fake_board.py). Must run before caltreat is imported.

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
os.environ["CALTREAT_BOARD_PORT"] = "15021"
os.environ["CALTREAT_HTTPS_PORT"] = "0"
sys.path.insert(0, str(Path(__file__).parent))

TMP = _tmp


def pytest_sessionfinish(session, exitstatus):
    """Remove the temp folder once the tests are done (atexit is the fallback)."""
    shutil.rmtree(_tmp, ignore_errors=True)
