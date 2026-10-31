"""The app's one settings file: ~/.config/caltreat/config.json (CALTREAT_CONFIG_DIR overrides the folder).

Schema-versioned, validated on load and upload, written atomically (temp file + rename). Missing keys take their
defaults, so a file from an older version still loads. The board names from the old boards.json are migrated in on
first start. Runtime state (lights on/off, throttle, ...) is not kept here, only configuration.

    cfg = Config.load()            # defaults if there is no file
    cfg.data["mix"]["horn"] = 90   # then cfg.save()
    errors = validate(obj)         # [] if obj is a good config
"""

import copy
import json
import os
import re
import tempfile
import threading
from pathlib import Path

VERSION = 1
DIR = Path(os.environ.get("CALTREAT_CONFIG_DIR") or Path.home() / ".config" / "caltreat")
FILE = "config.json"
OLD_BOARDS_FILE = "boards.json"

SPEAKERS = ("front", "inside", "rear")  # logical outputs; the intercom plays on INSIDE
CHANNELS = ("both", "left", "right")
MIX_KEYS = ("master", "horn", "bell", "motor", "doors", "air", "brakes")
TIMINGS = ("costume", "real")
BOARD_PINS = 8
RELAY_CHANNELS = 4

# Light functions: what the engineer page and the sequences drive. Outputs (relay channels, board GP pins) are mapped
# to a function; two outputs of one function with different phases (alt) alternate when it flashes.
# kind: manual = off / on / flash from the engineer page; doors = on while the doors are open, flashing while they
# move; crossing = flashing while a crossing signal sounds; horn = on while the horn sounds; bell = on while the
# bell rings. (id, default name, kind, symbol)
BUILTIN_FUNCTIONS = [
    ("head", "Headlights", "manual", "☀"),
    ("markers", "Markers", "manual", "◆"),
    ("interior", "Interior", "manual", "▢"),
    ("doors", "Door lights", "doors", "▯"),
    ("crossing", "Crossing flash", "crossing", "✕"),
    ("horn", "Horn lamp", "horn", "▲"),
    ("bell", "Bell lamp", "bell", "♫"),
]
KINDS = ("manual", "doors", "crossing", "horn", "bell")
ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
DEVICE_RE = re.compile(r"^(jack|none|usb#[1-9]|usb:[0-9]+(\.[0-9]+)*)$")
UID_RE = re.compile(r"^[0-9A-F]{4,32}$")


def defaults():
    return {
        "version": VERSION,
        "mix": {k: 100 for k in MIX_KEYS},  # % of the engine's default level
        "timing": "costume",
        "depart_horn": True,
        "speakers": {
            "front": {"device": "jack", "ch": "both", "level": 100},
            "inside": {"device": "usb#1", "ch": "both", "level": 100},
            "rear": {"device": "none", "ch": "both", "level": 100},
        },
        "volumes": {},  # ALSA card volume % by device ("jack", "usb:1.2"); unset = leave as it is
        "intercom": {"mic_gain_db": 0, "level": 80, "duck_db": -18, "timeout_s": 60},
        "functions": [{"id": i, "name": n, "kind": k, "symbol": s} for i, n, k, s in BUILTIN_FUNCTIONS],
        "horn_flash": True,  # headlights flash (alternate) with the horn, as ditch lights at crossings
        "relay": [{"fn": "head", "alt": False}, {"fn": "head", "alt": True},
                  {"fn": "markers", "alt": False}, {"fn": "markers", "alt": True}],
        "boards": {},  # chip ID -> {"name": "A", "outputs": [{"fn": id or None, "alt": bool} x 8]}
    }


def board_defaults(name):
    return {"name": name, "outputs": [{"fn": None, "alt": False} for _ in range(BOARD_PINS)]}


def _num(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def _output_errors(o, where, fn_ids):
    if not isinstance(o, dict):
        return [f"{where}: not an object"]
    errs = []
    if o.get("fn") is not None and o.get("fn") not in fn_ids:
        errs.append(f"{where}.fn: unknown function {o.get('fn')!r}")
    if not isinstance(o.get("alt", False), bool):
        errs.append(f"{where}.alt: not true/false")
    return errs


def validate(c):
    """[] if c is a valid config (after defaults are filled in), else a list of readable errors."""
    if not isinstance(c, dict):
        return ["not a JSON object"]
    errs = []
    v = c.get("version")
    if not isinstance(v, int) or v < 1:
        errs.append("version: missing or not a positive integer")
    elif v > VERSION:
        errs.append(f"version {v} is newer than this app understands ({VERSION})")
    mix = c.get("mix", {})
    if not isinstance(mix, dict):
        errs.append("mix: not an object")
    else:
        errs += [f"mix.{k}: not 0-150" for k, x in mix.items() if k not in MIX_KEYS or not _num(x, 0, 150)]
    if c.get("timing", "costume") not in TIMINGS:
        errs.append(f"timing: one of {', '.join(TIMINGS)}")
    if not isinstance(c.get("depart_horn", True), bool) or not isinstance(c.get("horn_flash", True), bool):
        errs.append("depart_horn / horn_flash: not true/false")
    sp = c.get("speakers", {})
    if not isinstance(sp, dict):
        errs.append("speakers: not an object")
    else:
        for k, s in sp.items():
            if k not in SPEAKERS or not isinstance(s, dict):
                errs.append(f"speakers.{k}: unknown speaker or not an object")
                continue
            if not isinstance(s.get("device"), str) or not DEVICE_RE.match(s["device"]):
                errs.append(f"speakers.{k}.device: not jack, none, usb#N or usb:<port>")
            if s.get("ch", "both") not in CHANNELS:
                errs.append(f"speakers.{k}.ch: one of {', '.join(CHANNELS)}")
            if not _num(s.get("level", 100), 0, 100):
                errs.append(f"speakers.{k}.level: not 0-100")
    vol = c.get("volumes", {})
    if not isinstance(vol, dict) or any(not DEVICE_RE.match(str(k)) or not _num(x, 0, 100) for k, x in vol.items()):
        errs.append("volumes: device -> 0-100")
    ic = c.get("intercom", {})
    if not isinstance(ic, dict):
        errs.append("intercom: not an object")
    else:
        for k, lo, hi in (("mic_gain_db", -20, 30), ("level", 0, 100), ("duck_db", -60, 0), ("timeout_s", 5, 600)):
            if k in ic and not _num(ic[k], lo, hi):
                errs.append(f"intercom.{k}: not {lo} to {hi}")
    fns = c.get("functions", [])
    fn_ids = set()
    builtin = {f[0] for f in BUILTIN_FUNCTIONS}  # always there (complete() adds them back)
    if not isinstance(fns, list):
        errs.append("functions: not a list")
        fns = []
    for i, f in enumerate(fns):
        if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not ID_RE.match(f["id"]):
            errs.append(f"functions[{i}].id: lowercase letters, digits, - or _ (1-32)")
            continue
        if f["id"] in fn_ids:
            errs.append(f"functions[{i}].id: {f['id']!r} used twice")
        fn_ids.add(f["id"])
        if not isinstance(f.get("name"), str) or not 0 < len(f["name"]) <= 40:
            errs.append(f"functions[{i}].name: 1-40 characters")
        if f.get("kind", "manual") not in KINDS:
            errs.append(f"functions[{i}].kind: one of {', '.join(KINDS)}")
        if not isinstance(f.get("symbol", ""), str) or len(f.get("symbol", "")) > 4:
            errs.append(f"functions[{i}].symbol: up to 4 characters")
    fn_ids |= builtin
    relay = c.get("relay", [{}] * RELAY_CHANNELS)
    if not isinstance(relay, list) or len(relay) != RELAY_CHANNELS:
        errs.append(f"relay: a list of {RELAY_CHANNELS} outputs")
    else:
        for i, o in enumerate(relay):
            errs += _output_errors(o, f"relay[{i}]", fn_ids)
    boards = c.get("boards", {})
    if not isinstance(boards, dict):
        errs.append("boards: not an object")
    else:
        names = set()
        for uid, b in boards.items():
            if not UID_RE.match(str(uid)) or not isinstance(b, dict):
                errs.append(f"boards.{uid}: not a chip ID (hex) -> object")
                continue
            if not isinstance(b.get("name"), str) or not 0 < len(b["name"]) <= 12:
                errs.append(f"boards.{uid}.name: 1-12 characters")
            elif b["name"] in names:
                errs.append(f"boards.{uid}.name: {b['name']!r} used twice")
            names.add(b.get("name"))
            outs = b.get("outputs", [])
            if not isinstance(outs, list) or len(outs) != BOARD_PINS:
                errs.append(f"boards.{uid}.outputs: a list of {BOARD_PINS}")
            else:
                for i, o in enumerate(outs):
                    errs += _output_errors(o, f"boards.{uid}.outputs[{i}]", fn_ids)
    return errs


def complete(c):
    """c with every missing key filled in from the defaults (c is not changed). Built-in functions always exist."""
    d = defaults()
    out = copy.deepcopy(c)
    for k, v in d.items():
        if k not in out:
            out[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and k in ("mix", "intercom"):
            out[k] = {**v, **out[k]}
    for k, v in d["speakers"].items():
        out["speakers"][k] = {**v, **out["speakers"].get(k, {})}
    have = {f["id"] for f in out["functions"]}
    out["functions"] = [f for f in d["functions"] if f["id"] not in have] + out["functions"]
    for f in out["functions"]:
        f.setdefault("kind", "manual")
        f.setdefault("symbol", "")
    for o in out["relay"]:
        o.setdefault("fn", None)
        o.setdefault("alt", False)
    for uid, b in out["boards"].items():
        b.setdefault("outputs", board_defaults(b.get("name", "?"))["outputs"])
        for o in b["outputs"]:
            o.setdefault("fn", None)
            o.setdefault("alt", False)
    out["version"] = VERSION
    return out


def parse(text):
    """Uploaded JSON text -> (completed config, []) or (None, errors)."""
    try:
        c = json.loads(text)
    except (ValueError, UnicodeDecodeError) as e:
        return None, [f"not JSON: {e}"]
    errs = validate(c)
    if errs:
        return None, errs
    c = complete(c)
    errs = validate(c)
    return (None, errs) if errs else (c, [])


def diff(old, new, path=""):
    """Readable list of what changes from old to new: ["mix.horn: 100 -> 90", ...]."""
    out = []
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new), key=str):
            p = f"{path}.{k}" if path else str(k)
            if k not in new:
                out.append(f"{p}: removed")
            elif k not in old:
                out.append(f"{p}: added {json.dumps(new[k], ensure_ascii=False)[:80]}")
            else:
                out += diff(old[k], new[k], p)
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for i, (a, b) in enumerate(zip(old, new)):
            out += diff(a, b, f"{path}[{i}]")
    elif old != new:
        out.append(f"{path}: {json.dumps(old, ensure_ascii=False)[:60]} -> {json.dumps(new, ensure_ascii=False)[:60]}")
    return out


class Config:
    """The loaded config plus where it lives. Change `data`, then `save()`; `replace()` swaps in a whole new one.
    Listeners (`on_change(fn)`) are called after every save/replace, from the caller's thread."""

    def __init__(self, data, folder=None):
        self.folder = Path(folder or DIR)
        self.data = data
        self.lock = threading.RLock()
        self.error = None
        self.listeners = []

    @property
    def path(self):
        return self.folder / FILE

    @classmethod
    def load(cls, folder=None):
        cfg = cls(defaults(), folder)
        try:
            raw = json.loads(cfg.path.read_text())
        except FileNotFoundError:
            raw = None
        except (OSError, ValueError) as e:
            cfg.error = f"{cfg.path} unreadable ({e}); using defaults"
            raw = None
        if raw is not None:
            errs = validate(raw)
            if errs:
                cfg.error = f"{cfg.path} invalid ({errs[0]}); using defaults"
            else:
                cfg.data = complete(raw)
        elif cfg.error is None:
            cfg._migrate()
        return cfg

    def _migrate(self):
        """First start: take the board names from the old boards.json (chip ID -> name)."""
        try:
            names = dict(json.loads((self.folder / OLD_BOARDS_FILE).read_text()))
        except (OSError, ValueError, TypeError):
            return
        for uid, name in names.items():
            if UID_RE.match(str(uid)) and isinstance(name, str):
                self.data["boards"][uid] = board_defaults(name[:12])
        self.save()

    def save(self):
        with self.lock:
            text = json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=self.folder)
                with os.fdopen(fd, "w") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
                self.error = None
            except OSError as e:
                self.error = f"could not save {self.path}: {e}"
        for fn in self.listeners:
            fn()

    def replace(self, data):
        with self.lock:
            self.data = complete(data)
        self.save()

    def reset(self):
        """Defaults, but the boards keep their names (so they don't swap letters)."""
        d = defaults()
        d["boards"] = {uid: board_defaults(b["name"]) for uid, b in self.data["boards"].items()}
        self.replace(d)

    def export(self):
        with self.lock:
            return json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"

    def on_change(self, fn):
        self.listeners.append(fn)

    def function(self, fid):
        return next((f for f in self.data["functions"] if f["id"] == fid), None)
