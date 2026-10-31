"""Recordings on the pages (recordings.py): the settings section (upload, ▶ / ■, loop, level, speakers, duck,
pin, rename, download, delete, zip backup and restore), the pinned buttons on the engineer page, and the HTTP
routes the browser uses (static/recordings.js decodes uploads to WAV first). Short words, details in tooltips.

    recordings_ui.setup(store, player)                 # once: routes
    recordings_ui.section(store, player, live, el=el, sbtn=sbtn, slider=slider)   # settings page
    recordings_ui.engineer(store, player)              # engineer page
"""

import asyncio
import json

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from nicegui import app, ui

from . import recordings as rec

SPK = (("front", "F", "Front (outside, front cab)"), ("inside", "I", "Inside (passengers)"),
       ("rear", "R", "Rear (outside, back)"))
SCRIPT = '<script src="/static/recordings.js"></script>'
# Drawn, not emoji: a browser without an emoji font shows emoji as empty boxes
ICON_PIN = ('<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l-1 6 3 3H7l3-3zM12 12v9"/></svg>')
ICON_BIN = ('<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 7V4h4v3M6 7l1 13h10l1-13'
            'M10 11v6M14 11v6"/></svg>')


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def mmss(s):
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


async def _body(request, limit):
    """The request body, or ValueError past `limit` bytes."""
    chunks, n = [], 0
    async for c in request.stream():
        n += len(c)
        if n > limit:
            raise ValueError(f"bigger than {limit // 1024 ** 2} MB")
        chunks.append(c)
    return b"".join(chunks)


def setup(store, player):
    @app.post("/api/recordings")
    async def upload(request: Request, name: str = "Recording"):
        try:
            data = await _body(request, rec.MAX_BYTES)
            m = await asyncio.to_thread(store.add, name, data)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"id": m["id"], "name": m["name"], "duration": m["duration"]}

    @app.get("/api/recordings/{rid}.wav")
    async def download(rid: str):
        m = store.get(rid)
        if m is None:
            return Response(status_code=404)
        return FileResponse(store.path(rid), media_type="audio/wav", filename=f"{m['name']}.wav")

    @app.get("/api/recordings.zip")
    async def download_zip():
        data = await asyncio.to_thread(store.zip_bytes)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="caltreat-recordings.zip"'})

    @app.post("/api/recordings/restore")
    async def restore(request: Request):
        try:
            data = await _body(request, rec.MAX_ZIP)
            n = await asyncio.to_thread(store.restore_zip, data)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"count": n}


def section(store, player, live, el, sbtn, slider):
    """The settings page's recordings section (el / sbtn / slider: the page's own helpers)."""
    shown = [store.version]

    def tick():
        if store.version != shown[0]:
            shown[0] = store.version
            rows.refresh()
    live.append(tick)

    mine = []  # this section's row updaters in `live`: dropped when the rows are drawn again

    @ui.refreshable
    def rows():
        for fn in mine:
            if fn in live:
                live.remove(fn)
        mine.clear()
        recs = store.list()
        if not recs:
            el("div", "mapdia", "No recordings yet: ＋ to upload")
        for m in recs:
            row(m)

    def row(m):
        rid = m["id"]
        with el("div", "rec"):
            with el("div", "srow"):
                with el("div", "nm"):
                    name = el("input", "txt", type="text", value=m["name"], maxlength="40",
                              title="Name (shown on the engineer page)")
                    name.props(f'aria-label="Recording name {_esc(m["name"])}"')
                    name.on("change", lambda e: rename(rid, e.args), js_handler="(e) => emit(e.target.value)")
                    ui.html(mmss(m["duration"]), sanitize=False, tag="small")
                with el("div", "ctlz"):
                    play = sbtn("▶", lambda: player.toggle(rid), "Play / stop")
                    loop = sbtn("⟳", lambda: flip(rid, "loop"), "Loop: repeat until stopped")
                    chips = {}
                    for sp, short, tip in SPK:
                        chips[sp] = sbtn(short, lambda sp=sp: speaker(rid, sp), f"Play on {tip}", "chip")
                    pin = sbtn(ICON_PIN, lambda: flip(rid, "pinned"), "Pin to the engineer page")
                    el("a", "sbtn", "⤓", "Download (wav)", href=f"/api/recordings/{rid}.wav")
                    sbtn(ICON_BIN, lambda: delete(rid, m["name"]), "Delete", "warn")
            bar = el("div", "recbar")
            with bar:
                fill = ui.element("i")
            slider("Level", 0, 150, m["level"], lambda v: store.update(rid, level=v), "Level, % (speaker levels "
                   "apply on top)")
            slider("Duck train", -60, 0, m["duck_db"], lambda v: store.update(rid, duck_db=v),
                   "Train sounds on its speakers while it plays (0 = not ducked)", "dB")

        def show(play=play, loop=loop, chips=chips, pin=pin, fill=fill, bar=bar):
            cur = store.meta(rid)
            if cur is None:
                return
            st = player.state()
            on = rid in st
            _set(play, "■" if on else "▶", "sbtn on" if on else "sbtn")
            _set(loop, None, "sbtn on" if cur["loop"] else "sbtn")
            _set(pin, None, "sbtn on" if cur["pinned"] else "sbtn")
            for sp, c in chips.items():
                _set(c, None, "sbtn chip on" if sp in cur["speakers"] else "sbtn chip")
            _set(fill, None, None, f"width: {int(st.get(rid, 0) * 100)}%")
            _set(bar, None, "recbar on" if on else "recbar")
        live.append(show)
        mine.append(show)
        show()

    def rename(rid, name):
        store.update(rid, name=name)

    def flip(rid, key):
        m = store.meta(rid)
        if m:
            store.update(rid, **{key: not m[key]})

    def speaker(rid, sp):
        m = store.meta(rid)
        if m:
            sps = set(m["speakers"]) ^ {sp}
            store.update(rid, speakers=[s for s, _, _ in SPK if s in sps])

    def delete(rid, name):
        with ui.dialog() as d, el("div", "dlg"):
            el("b", "", f"Delete {_esc(name)}?")
            with el("div", "ctlz"):
                sbtn(ICON_BIN, lambda: (player.stop(rid), store.delete(rid), d.close()), "Delete for good", "warn")
                sbtn("✕", d.close, "Cancel")
        d.open()

    client = ui.context.client
    if not getattr(client, "_recs_script", False):  # the section re-renders; the script loads once per page
        client._recs_script = True
        ui.add_head_html(SCRIPT)
    with el("section", "ctl set recs-set"):
        with el("h2", "", title="Recordings: play your own sounds on any speakers. Upload wav, mp3, m4a, ogg, "
                                "flac (up to 10 min); the browser converts them"):
            ui.html("♪ Recordings", sanitize=False, tag="span")
            with el("span", "acts"):
                el("button", "sbtn", "＋", "Upload recordings (several at once)", type="button") \
                    .on("click", js_handler="() => recs.pick()")
                el("a", "sbtn", "⤓", "Download all recordings (zip)", href="/api/recordings.zip",
                   download="caltreat-recordings.zip")
                el("button", "sbtn", "⤒", "Restore recordings from a downloaded zip (adds them; same ones are "
                                          "replaced)", type="button").on("click", js_handler="() => recs.pickZip()")
        el("div", "recmsg", "", role="status")
        rows()



def _set(e, text=None, cls=None, style=None):
    """Update an element only when something changed (this runs every 0.5 s)."""
    shown = e.__dict__.setdefault("_rec_shown", {})
    if text is not None and shown.get("t") != text:
        shown["t"] = text
        e.clear()
        with e:
            ui.html(text, sanitize=False, tag="span")
    if cls is not None and shown.get("c") != cls:
        shown["c"] = cls
        e.classes(replace=cls)
    if style is not None and shown.get("s") != style:
        shown["s"] = style
        e.style(replace=style)


def state(store, player):
    """What the engineer page shows: the pinned recordings and what plays (progress 0-1)."""
    return {"pinned": [{"id": m["id"], "name": m["name"], "dur": m["duration"], "loop": m["loop"]}
                       for m in store.list() if m["pinned"]],
            "playing": {k: round(v, 3) for k, v in player.state().items()}}


def engineer(store, player):
    """The engineer page's recordings: pinned buttons (tap play / stop), a stop-all; shared by every browser."""
    ui.add_head_html(SCRIPT)

    def on(e):
        a, rid = (e.args or {}).get("a"), (e.args or {}).get("id")
        try:
            if a == "toggle":
                player.toggle(rid)
            elif a == "stop_all":
                player.stop_all()
        except KeyError:
            pass
        push()

    ui.on("rec", on)
    sent = [None]

    def push():
        st = state(store, player)
        if st != sent[0]:
            sent[0] = st
            ui.run_javascript(f"window.recs && recs.update({json.dumps(st)})")

    ui.timer(0.2, push)
