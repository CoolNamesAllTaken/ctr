"""Settings sliders keep where they were dragged: while the page updates itself, after a reload, in a second browser
and after an app restart, and config.json holds the values. Runs the app in a subprocess and drives it with
Playwright (skipped without it: pip install playwright; with Chromium's libraries, e.g. LD_LIBRARY_PATH=...)."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from caltreat.sidecar import find_node

sync_api = pytest.importorskip("playwright.sync_api")

SRC = Path(__file__).resolve().parents[1] / "src"
CARDS = ("3.5 mm jack", "USB")  # sound card volumes: fake ones aren't kept across a restart


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def app(tmp_path):
    port = free_port()
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")},  # else NiceGUI runs as a test
           "CALTREAT_PORT": str(port), "CALTREAT_CONFIG_DIR": str(tmp_path / "config"),
           "CALTREAT_FAKE_AUDIO": str(tmp_path / "audio"), "CALTREAT_BOARD_NET": "127.0.10.",
           "CALTREAT_HTTPS_PORT": "0", "PYTHONPATH": str(SRC)}
    (tmp_path / "audio").mkdir()
    procs = []

    def start():
        log = open(tmp_path / "app.log", "a")
        p = subprocess.Popen([sys.executable, "-m", "caltreat"], env=env, cwd=tmp_path,
                             stdout=log, stderr=subprocess.STDOUT)
        procs.append(p)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/settings", timeout=1)
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("app didn't start:\n" + (tmp_path / "app.log").read_text()[-2000:])

    def stop():
        procs[-1].terminate()
        procs[-1].wait(10)

    start()
    yield f"http://127.0.0.1:{port}/settings", start, stop, tmp_path / "config" / "config.json"
    for p in procs:
        if p.poll() is None:
            p.kill()


def values(page):
    page.wait_for_timeout(1200)  # let the 0.5 s refresh run a few times
    return page.locator("input[type=range]").evaluate_all("es => es.map(e => [e.getAttribute('aria-label'), e.value])")


def drag_all(page):
    """Drag every slider to 30 % or 55 % of its track (alternating; no default sits there); the values they show afterwards."""
    sliders = page.locator("input[type=range]")
    for i in range(sliders.count()):
        r = sliders.nth(i)
        r.scroll_into_view_if_needed()
        box = r.bounding_box()
        y = box["y"] + box["height"] / 2
        page.mouse.move(box["x"] + box["width"] / 2, y)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * (0.3 if i % 2 else 0.55), y, steps=6)
        page.mouse.up()
        page.wait_for_timeout(150)
    return values(page)


@pytest.mark.parametrize("viewport", [{"width": 390, "height": 844}, {"width": 1280, "height": 900}],
                         ids=["phone", "desktop"])
def test_sliders_keep_their_values(app, viewport):
    url, start, stop, cfg_file = app
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:  # no browser here
            pytest.skip(f"no Chromium: {e}")
        page = browser.new_page(viewport=viewport)
        page.goto(url)
        before = values(page)
        dragged = drag_all(page)
        assert all(a != b for (_, a), (_, b) in zip(before, dragged)), f"some slider didn't move: {before} -> {dragged}"
        page.wait_for_timeout(2000)
        assert values(page) == dragged  # no snapping back while the page refreshes
        page.reload()
        assert values(page) == dragged
        other = browser.new_page(viewport=viewport)
        other.goto(url)
        assert values(other) == dragged

        saved = json.loads(cfg_file.read_text())
        mix = saved["mix12"] if find_node()[0] else saved["mix"]  # the v12 engine's Mix, or the old engine's
        assert [str(v) for v in mix.values()] == [v for _, v in dragged[:len(mix)]]

        stop()
        start()
        page.goto(url)
        kept = [(k, v) for k, v in dragged if not k.startswith(CARDS)]
        assert [(k, v) for k, v in values(page) if not k.startswith(CARDS)] == kept
        browser.close()
