"""Branding and the dark / light theme (static/theme.css, static/theme.js): what goes in every page's <head>, and
the theme button for headers drawn by NiceGUI (the engineer page's plain HTML header gets it from theme.js)."""

from pathlib import Path

ICON = Path(__file__).parent / "static" / "brand" / "icon.svg"  # the website's icon (favicon)
HEAD = """
<meta name="theme-color" content="#14171b">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Caltreat">
<link rel="manifest" href="/static/manifest.webmanifest">
<link rel="icon" type="image/svg+xml" href="/static/brand/icon.svg">
<link rel="apple-touch-icon" href="/static/brand/apple-touch-icon.png">
<link rel="stylesheet" href="/static/theme.css">
<script src="/static/theme.js"></script>"""


def button(el):
    """☀ / ☾ (shows the theme it switches to); el: the page's element helper."""
    b = el("button", "iconbtn themebtn", '<span class="th-d" aria-hidden="true">☀</span>'
                                         '<span class="th-l" aria-hidden="true">☾</span>',
           "Dark / light theme", type="button", aria_label="Dark / light theme")
    b.on("click", js_handler="() => theme.toggle()")
    return b
