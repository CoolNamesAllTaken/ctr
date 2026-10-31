"""Recordings in the browser: upload a WAV and an MP3 on the settings page (decoded by the browser), send the MP3 to
the front speaker only and pin it, play it from the engineer page, and find it on the front card's PCM (fake audio).
Also: a file that isn't audio is refused, and the theme toggle is remembered. Playwright, as test_sliders.py."""

import json
import struct
from pathlib import Path

import numpy as np
import pytest
from test_sliders import app  # noqa: F401 (the app in a subprocess)

sync_api = pytest.importorskip("playwright.sync_api")

MP3 = Path(__file__).parent / "data" / "tones-660-990.mp3"  # 1 s: 660 Hz left, 990 Hz right (made with lamejs)
SR = 48000


def wav(path, f=1000, secs=1.0, rate=22050):
    x = (0.5 * np.sin(2 * np.pi * f * np.arange(int(secs * rate)) / rate) * 32767).astype("<i2").tobytes()
    path.write_bytes(b"RIFF" + struct.pack("<I", 36 + len(x)) + b"WAVEfmt " +
                     struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(x)) + x)
    return path


def level_at(x, f):
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) / len(x) * 4
    return s[np.argmin(abs(np.fft.rfftfreq(len(x), 1 / SR) - f))]


def test_upload_pin_and_play(app, tmp_path):  # noqa: F811
    url, _, _, cfg_file = app
    base = url.rsplit("/", 1)[0]
    audio = cfg_file.parent.parent / "audio"
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:
            pytest.skip(f"no Chromium: {e}")
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(url)
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"  # the default
        (tmp_path / "junk.mp3").write_text("not audio")
        with page.expect_file_chooser() as fc:
            page.locator('button[title^="Upload recordings"]').click()
        fc.value.set_files([wav(tmp_path / "beep.wav"), MP3, tmp_path / "junk.mp3"])
        page.wait_for_function("/✓|✗/.test(document.querySelector('.recmsg')?.textContent || '')", timeout=30000)
        msg = page.locator(".recmsg").text_content()
        assert "✓ 2" in msg and "junk.mp3" in msg
        page.wait_for_selector('.rec input[value="tones-660-990"]')
        row = page.locator(".rec", has=page.locator('input[value="tones-660-990"]'))
        row.locator("button.chip", has_text="I").click()  # off inside
        row.locator("button.chip", has_text="F").click()  # on front
        row.locator('button[title^="Pin"]').click()
        page.wait_for_timeout(1500)  # saved ~0.5 s after the change
        meta = {m["name"]: m for m in json.loads(cfg_file.read_text())["recordings"].values()}
        assert meta["tones-660-990"]["speakers"] == ["front"] and meta["tones-660-990"]["pinned"]
        assert meta["beep"]["duration"] == pytest.approx(1.0, abs=0.01)

        eng = browser.new_page(viewport={"width": 390, "height": 844})
        eng.goto(base + "/")
        btn = eng.locator("#recs .recbtn", has_text="tones-660-990")
        btn.click()
        eng.wait_for_selector("#recs .recbtn.on")
        eng.wait_for_timeout(800)
        jack = sorted(audio.glob("jack-*.raw"), key=lambda p: p.stat().st_mtime)[-1]
        x = np.frombuffer(jack.read_bytes()[: jack.stat().st_size // 4 * 4], "<i2").reshape(-1, 2) / 32768
        seg = x[-int(0.5 * SR):, 0]
        assert level_at(seg, 660) > 0.05 and level_at(seg, 990) > 0.05  # both channels of the MP3, mixed to mono
        inside = sorted(audio.glob("usb-*.raw"), key=lambda p: p.stat().st_mtime)
        if inside:  # inside (USB #1) must not get it
            y = np.frombuffer(inside[-1].read_bytes()[: inside[-1].stat().st_size // 4 * 4], "<i2").reshape(-1, 2)
            assert level_at(y[-int(0.5 * SR):, 0] / 32768, 660) < 0.005
        eng.wait_for_selector("#recs .recbtn.on", state="detached", timeout=5000)  # 1 s file: ends by itself

        eng.locator(".themebtn").click()
        eng.reload()
        assert eng.evaluate("document.documentElement.dataset.theme") == "light"
        browser.close()
