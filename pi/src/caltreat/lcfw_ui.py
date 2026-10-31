"""The firmware part of the settings page's lighting controller section (lcfw.py): each board's firmware and register
map versions (⚠ when it doesn't run the chosen image), ⤓ to update one board or all, the image (⌂ deployed with
the app, or ⤒ uploaded), ↓ to allow older images, a progress bar and ✓ / ✗. Words are kept short; details are in tooltips.

    lcfw_ui.section(finder, live)   # inside the settings page; live: callables run every 0.5 s
"""

from nicegui import background_tasks, ui

from . import lcfw

# Shared by every browser, like the updates themselves
state = {"upload": None, "upload_name": "", "use_upload": False, "allow_downgrade": False, "error": ""}

STAGE = {"check": "…", "connect": "…", "send": "⤓", "verify": "✓?", "reboot": "⟳", "done": "✓", "failed": "✗"}
CSS = """
.fw .srow .nm small { white-space: nowrap; }
.fwbar { flex: 1 1 50px; min-width: 40px; height: 10px; border-radius: 5px; background: var(--lcd); border: 1px solid var(--bezel); overflow: hidden; }
.fwbar i { display: block; height: 100%; width: 0; background: var(--amber); transition: width .4s; }
.fwbar.ok i { background: var(--green); } .fwbar.bad i { background: var(--red); }
.fwres { font: 700 16px/1 var(--display); min-width: 18px; text-align: center; }
.fwres.ok { color: var(--green); } .fwres.bad { color: #ff8a80; }
.st.warn { color: var(--amber); }
"""


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def el(tag, cls="", text=None, title=None):
    e = ui.element(tag).classes(cls)
    if title:
        e.props(f'title="{_esc(title)}"')
    if text is not None:
        with e:
            ui.html(_esc(text), sanitize=False, tag="span")
    return e


def btn(text, on_tap, title, cls="sbtn"):
    b = el("button", cls, text, title).props('type="button"')
    b.on("click", lambda e: on_tap())
    return b


def upd(e, text=None, title=None, cls=None, disabled=None, style=None, visible=None):
    """Update an element, sending only what changed (this runs every 0.5 s)."""
    shown = e.__dict__.setdefault("_fw_shown", {})

    def changed(k, v):
        if v is None or shown.get(k) == v:
            return False
        shown[k] = v
        return True

    if changed("text", text):
        e.clear()
        with e:
            ui.html(_esc(text), sanitize=False, tag="span")
    if changed("title", title):
        e.props(f'title="{_esc(title)}"')
    if changed("cls", cls):
        e.classes(replace=cls)
    if changed("disabled", disabled):
        e.props("disabled") if disabled else e.props(remove="disabled")
    if changed("style", style):
        e.style(replace=style)
    if changed("visible", visible):
        e.set_visibility(visible)


def image():
    """The image to install: the uploaded one if chosen, else the one deployed with the app."""
    return state["upload"] if state["use_upload"] and state["upload"] else lcfw.bundled()


def image_title(img):
    if img is None:
        return ("No image: deploy with pi/deploy.sh --with-lc-firmware <firmware.bin>, or upload one" +
                (f" ({state['error']})" if state["error"] else ""))
    src = f"uploaded {state['upload_name']}" if img is state["upload"] else f"deployed {lcfw.BUNDLED.name}"
    return f"Image {img.version_str} · {src} · {len(img.data):,} bytes · SHA-256 {img.sha256.hex()[:16]}…"


def board_title(b, fw):
    if not fw:
        return f"Board {b.name}: firmware not read yet (not connected) · ID {b.uid}"
    return (f"Board {b.name} · firmware {fw['fw_version']} · register map {fw['regmap']}"
            f"{' (app expects ' + str(lcfw.REGMAP_VERSION) + ')' if fw['regmap'] != lcfw.REGMAP_VERSION else ''}"
            f" · partition {fw['boot_partition']}{' · on trial' if fw['trial'] else ''}"
            f" · MAC {b.mac or fw['mac']} · ID {b.uid} · {b.host}")


def section(finder, live):
    ui.add_css(CSS)

    async def run_one(b):
        img = image()
        if img is None:
            return
        try:
            await lcfw.update(finder, b, img, state["allow_downgrade"])
        except (lcfw.UpdateError, OSError) as e:
            print(f"caltreat: firmware update of board {b.name}: {e}")

    async def run_all():
        img = image()
        if img is None:
            return
        res = await lcfw.update_all(finder, img, state["allow_downgrade"])
        for uid, r in res.items():
            if isinstance(r, Exception):
                print(f"caltreat: firmware update of board {uid}: {r}")

    def start_one(b):
        if image() is not None and not any(j.running for j in lcfw.jobs.values()):
            background_tasks.create(run_one(b), name=f"lcfw-{b.uid}")

    def start_all():
        if image() is not None and not any(j.running for j in lcfw.jobs.values()):
            background_tasks.create(run_all(), name="lcfw-all")

    async def on_upload(e):
        data = await e.file.read()
        try:
            img = lcfw.load(data, e.file.name)
        except ValueError as err:
            state["error"] = str(err).split(": ", 1)[-1]
            ui.notify(f"✗ {state['error']}", type="negative")
            return
        state.update(upload=img, upload_name=e.file.name, use_upload=True, error="")

    def toggle(key):
        state[key] = not state[key]

    with el("div", "fw"):
        with el("div", "boardhd"):
            el("b", "", "Firmware", "Lighting controller firmware: update over the network. A board's lights go off "
                                    "while it updates; a failed update leaves the old firmware running")
        with el("div", "rows"):
            with el("div", "srow"):
                el("div", "nm", "Image")
                with el("div", "ctlz"):
                    img_chip = el("span", "st", "—")
                    src_btn = btn("⌂", lambda: toggle("use_upload"), "")
                    up = ui.upload(on_upload=on_upload, auto_upload=True, max_files=1).props('accept=".bin"')
                    up.style("display: none")
                    pick = el("button", "sbtn", "⤒", "Upload a firmware.bin (.pio/build/app/firmware.bin)")
                    pick.props('type="button"')
                    pick.on("click", js_handler=f"() => getElement({up.id}).pickFiles()")  # in the tap: file dialog
                    dn_btn = btn("↓", lambda: toggle("allow_downgrade"), "")
                    all_btn = btn("⤓ All", start_all, "")

            def show_image():
                img = image()
                upd(img_chip, img.version_str if img else "—", image_title(img), "st" if img else "st warn")
                has_up = state["upload"] is not None
                deployed = not (state["use_upload"] and has_up)
                upd(src_btn, "⌂", ("Using the image deployed with the app; tap for the uploaded one" if deployed else
                                   "Using the uploaded image; tap for the one deployed with the app"),
                    "sbtn on" if deployed else "sbtn", visible=has_up)
                upd(dn_btn, "↓", "Allow older images (downgrade): " + ("on" if state["allow_downgrade"] else
                                                                           "off, a board refuses an older image"),
                         "sbtn warn" if state["allow_downgrade"] else "sbtn")
                busy = any(j.running for j in lcfw.jobs.values())
                todo = [b for b in finder.found if not (getattr(b, "fw", None) and img and b.fw["fw"] == img.version)]
                upd(all_btn, title=f"Update all boards not running {img.version_str}, one after the other "
                    f"({len(todo)})" if img else "No image", disabled=not (img and todo and not busy))
            live.append(show_image)
            show_image()

            for b in finder.found:
                with el("div", "srow"):
                    with el("div", "nm"):
                        ui.html(_esc(b.name), sanitize=False, tag="span")
                        ui.html(_esc(b.uid[-6:]), sanitize=False, tag="small")
                    with el("div", "ctlz"):
                        chip = el("span", "st", "—")
                        reg = el("span", "st", "r?")
                        go = btn("⤓", lambda b=b: start_one(b), "")
                        bar = el("div", "fwbar", title="")
                        with bar:
                            fill = ui.element("i")
                        res = el("span", "fwres", "")
                        stop = btn("✕", lambda b=b: lcfw.cancel(b.uid), "Cancel the update (while sending)",
                                   "sbtn warn")

                def show_board(b=b, chip=chip, reg=reg, go=go, bar=bar, fill=fill, res=res, stop=stop):
                    fw, img = getattr(b, "fw", None), image()
                    title = board_title(b, fw)
                    differs = bool(fw and img and fw["fw"] != img.version)
                    upd(chip, (f"{fw['fw_version']}{' ⚠' if differs else ''}" if fw else "—"),
                             title + (f" · not the image ({img.version_str})" if differs else ""),
                             "st warn" if differs or (fw and fw["trial"]) else "st")
                    upd(reg, f"r{fw['regmap']}" if fw else "r?", f"Register map {fw['regmap'] if fw else '?'} "
                             f"(app: {lcfw.REGMAP_VERSION})",
                             "st warn" if fw and fw["regmap"] != lcfw.REGMAP_VERSION else "st")
                    job = lcfw.jobs.get(b.uid)
                    busy = any(j.running for j in lcfw.jobs.values())
                    can = img is not None and not busy and b.connected and fw is not None and \
                        (img.version != fw["fw"]) and (img.version > fw["fw"] or state["allow_downgrade"])
                    why = ("No image" if img is None else "Updating" if busy else "Not connected" if not b.connected
                           else "Already runs this image" if fw and img.version == fw["fw"]
                           else "Older image: allow ↓ first" if fw and img.version < fw["fw"] and
                           not state["allow_downgrade"] else f"Update to {img.version_str}")
                    upd(go, title=why, disabled=not can)
                    if job:
                        ok = "ok" if job.stage == "done" else "bad" if job.stage == "failed" else ""
                        upd(fill, style=f"width: {int(job.fraction * 100)}%")
                        upd(bar, title=f"{job.target}: {job.stage} {job.sent:,} / {job.total:,} bytes",
                            cls=f"fwbar {ok}", visible=True)
                        upd(res, STAGE[job.stage], f"{job.target}: {job.message or job.stage}", f"fwres {ok}")
                        upd(stop, visible=job.stage in ("connect", "send"))
                    else:
                        upd(bar, visible=False)
                        upd(res, " ", " ")
                        upd(stop, visible=False)
                live.append(show_board)
                show_board()
