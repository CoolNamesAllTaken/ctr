"""The two cars, A and B, and the direction of travel (the reverser): "A" = car A leads, "B" = car B leads.

Speakers: each car has an exterior and an interior speaker, each on one half (L or R) of a USB DAC. The leading
car's exterior plays the FRONT bus (horn, bell, exterior sounds); the trailing car's exterior plays REAR (bell and
exterior sounds, no horn); both interiors play INSIDE (with the intercom). Recordings and the intercom keep
targeting the buses, so "front" follows the direction.

Lights: per car a headlight, two ditch lights and markers, as 49 CFR 229.125 has them. Leading car: headlight and
ditch lights on, steady; the ditch lights flash alternately (L / R) when the horn sounds and through a crossing
(229.125(d): the alternating flash comes on with the horn at grade crossings and may continue until the train is
past; here it starts with the horn or the Crossing sequence and stops `hold_s` (default 15 s) after the last horn).
Markers off. Trailing car: markers on, headlight and ditch lights off.
"""

PHYS = ("a_ext", "a_int", "b_ext", "b_int")
PHYS_LABEL = {"a_ext": ("A ext", "Car A, outside"), "a_int": ("A int", "Car A, inside"),
              "b_ext": ("B ext", "Car B, outside"), "b_int": ("B int", "Car B, inside")}
DIRECTIONS = ("A", "B")
STANDSTILL_MPH = 0.3  # the reverser moves only below this, with the power off
MPH_PER_SPEED = 79  # engine speed 0-1 -> mph (as the engineer page)
RATE_MIN, RATE_MAX = 40, 180  # flashes / min per lamp (49 CFR 229.125(d))
HOLD_MAX = 60  # s the ditch lights may keep flashing after the last horn


def role(phys, direction):
    """The bus a physical speaker plays: "front", "inside" or "rear"."""
    if phys.endswith("_int"):
        return "inside"
    leading = phys[0] == direction.lower()
    return "front" if leading else "rear"


def default_speakers():
    """Two USB DACs by port order: #1 L = A ext, #1 R = A int, #2 L = B ext, #2 R = B int."""
    return {"a_ext": {"device": "usb#1", "ch": "left", "level": 100},
            "a_int": {"device": "usb#1", "ch": "right", "level": 100},
            "b_ext": {"device": "usb#2", "ch": "left", "level": 100},
            "b_int": {"device": "usb#2", "ch": "right", "level": 100}}


OLD_DEFAULT_SPEAKERS = {"front": {"device": "jack", "ch": "both", "level": 100},
                        "inside": {"device": "usb#1", "ch": "both", "level": 100},
                        "rear": {"device": "none", "ch": "both", "level": 100}}


def migrate_speakers(old):
    """Speakers from a version 1 config (front / inside / rear). Untouched defaults become the new defaults (the
    two-DAC layout); a configured setup keeps its cards: front -> A ext, inside -> A int, rear -> B ext (car A leads,
    so they play what they did), B int off."""
    if not isinstance(old, dict) or not set(old) & {"front", "inside", "rear"}:
        return old
    full = {k: {**v, **old.get(k, {})} for k, v in OLD_DEFAULT_SPEAKERS.items()}
    if full == OLD_DEFAULT_SPEAKERS:
        return default_speakers()
    return {"a_ext": full["front"], "a_int": full["inside"], "b_ext": full["rear"],
            "b_int": {"device": "none", "ch": "both", "level": 100}}


def standstill(speed, throttle):
    """The reverser may move: power off and (practically) stopped."""
    return throttle <= 0.001 and speed * MPH_PER_SPEED < STANDSTILL_MPH


# ---- lights ----
# (id, name, kind, symbol, car, side)
CAR_FUNCTIONS = [
    (f"{c}_{k}", f"{c.upper()} {n}", kind, sym, c, side)
    for c in ("a", "b")
    for k, n, kind, sym, side in (("head", "headlight", "car_head", "☀", None),
                                  ("ditch_l", "ditch L", "car_ditch", "◐", "l"),
                                  ("ditch_r", "ditch R", "car_ditch", "◑", "r"),
                                  ("markers", "markers", "car_markers", "◆", None))
]
CAR_KINDS = ("car_head", "car_ditch", "car_markers")
KIND_LABEL = {"car_head": "headlight: on at the leading end",
              "car_ditch": "ditch light: leading end, steady; flashing with the horn and at crossings",
              "car_markers": "markers: on at the trailing end"}


def car_function_defs():
    out = []
    for fid, name, kind, sym, car, side in CAR_FUNCTIONS:
        f = {"id": fid, "name": name, "kind": kind, "symbol": sym, "car": car}
        if side:
            f["side"] = side
        out.append(f)
    return out


# Relay shield: channels 1-2 white (headlights), 3-4 red (markers)
DEFAULT_RELAY = [{"fn": "a_head", "alt": False}, {"fn": "b_head", "alt": False},
                 {"fn": "a_markers", "alt": False}, {"fn": "b_markers", "alt": False}]
OLD_DEFAULT_RELAY = [{"fn": "head", "alt": False}, {"fn": "head", "alt": True},
                     {"fn": "markers", "alt": False}, {"fn": "markers", "alt": True}]


def default_board_outputs(name, pins):
    """A board named A or B (they are named in the order they are found) drives its car's lamps on GP0-GP3, the
    MOSFET module's lights 1-4: ditch L, ditch R, headlight, markers. Others: nothing mapped."""
    out = [{"fn": None, "alt": False} for _ in range(pins)]
    c = str(name).lower()
    if c in ("a", "b"):
        for i, k in enumerate(("ditch_l", "ditch_r", "head", "markers")[:pins]):
            out[i]["fn"] = f"{c}_{k}"
    return out


def car_light(f, direction, lights_on, now, ditch, horn_recent, crossing):
    """(phase A, phase B) for a car function (f: its config entry). ditch: {"rate", "hold_s"}; horn_recent: the horn
    sounds or sounded within hold_s; crossing: the Crossing sequence or signal is running."""
    if not lights_on:
        return False, False
    leading = f.get("car") == direction.lower()
    kind = f["kind"]
    if kind == "car_markers":
        on = not leading
        return on, on
    if not leading:
        return False, False
    if kind == "car_head":
        return True, True
    # ditch light: L flashes on one phase, R on the other
    half = 30.0 / min(RATE_MAX, max(RATE_MIN, ditch.get("rate", 60)))  # s per half period
    phase = int(now / half) % 2 == 0
    if f.get("side") == "r":
        phase = not phase
    on = phase if horn_recent or crossing else True
    return on, on
