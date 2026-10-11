"""
The validator's "hand holding a card" icon as smooth, uniform-width strokes.

Each part of the drawing is modelled as what it represents, and fitted to a
trace of the reference photo (gen_assets.trace_tap_icon: a 120x127 crop of
https://www.sfcta.org/sites/default/files/2024-07/229027_LRV4_Interior_7.jpg):

  card        rounded quadrilateral; each visible side is a straight line fitted
              to the trace's centre line, corners are where neighbours meet
              (the photo's slight perspective means the sides are not quite
              perpendicular)
  fingertips  three loops on the card's left edge; start from the trace's finger
              holes and outline, then shape (tilt, height, centre, reach) is
              refined against the trace with the line weight held fixed
  thumb, back of hand, wrist
              free curves: a hand-placed template, each sample snapped to the
              trace's centre line, then Gaussian-smoothed

Layering reproduces what is in front of what: hand lines, then the card
(filled, hiding the lines' ends behind it), then the thumb (filled, hiding the
card edge behind it). All coordinates are in the 660x702 frame of
assets/ref_hand_icon_sfcta.png (the trace at 6x, offset by 30 px).
"""
from collections import deque

import numpy as np
from PIL import Image, ImageDraw

SCALE = 6           # trace resolution (x source pixels)
OFF = 30            # trace frame = reference frame + 30 px
FRAME = (660, 702)  # reference frame size (w, h)
LINE_WIDTH = 30.0   # best overlap with the reference (26..32 tried)
REACH_ADJ = 4.0     # fingertips: measured reach + this (best overlap)
CORNER_R = 26       # card corner radius
THUMB_SIGMA = 9.0   # smoothing of the thumb outline

# Hand-placed templates (reference frame): what each stroke is, roughly where.
CARD0 = dict(cx=228, cy=288, w=234, h=405, ang=-12.8, r=CORNER_R)
BACK0 = [(304, 162), (338, 188), (390, 240), (448, 312), (508, 398), (558, 468), (600, 536)]
WRIST0 = [(262, 450), (300, 505), (340, 545), (400, 585), (478, 630)]
THUMB0 = [(420, 322), (380, 292), (338, 262), (300, 242), (272, 248), (262, 268), (276, 292),
          (312, 324), (346, 360), (368, 385), (380, 410), (384, 445), (391, 485), (405, 525)]

NB = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]  # clockwise from N


# ---------------------------------------------------------------- geometry helpers

def catmull_rom(pts, steps=24):
    p = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = (np.array(v, dtype=float) for v in p[i - 1:i + 3])
        for t in np.linspace(0, 1, steps, endpoint=False):
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(np.array(pts[-1], dtype=float))
    return np.array(out)


def resample(p, step=2.0):
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
    t = np.arange(0, d[-1] + 1e-9, step)
    return np.c_[np.interp(t, d, p[:, 0]), np.interp(t, d, p[:, 1])]


def smooth(p, sigma=9.0, step=2.0):
    """Gaussian-smooth an open path; ends are anchored by point reflection."""
    p = resample(np.asarray(p, float), step)
    n = len(p)
    r = int(3 * sigma / step) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) * step / sigma) ** 2)
    k /= k.sum()
    q = np.empty((n + 2 * r, 2))
    for j, i in enumerate(range(-r, n + r)):
        if i < 0:
            q[j] = 2 * p[0] - p[min(-i, n - 1)]
        elif i >= n:
            q[j] = 2 * p[-1] - p[max(2 * (n - 1) - i, 0)]
        else:
            q[j] = p[i]
    return np.c_[np.convolve(q[:, 0], k, "valid"), np.convolve(q[:, 1], k, "valid")]


def nearest_dist(a, b):
    return np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)).min(1)


def to_local(c, xy):
    t = np.radians(c["ang"])
    d = np.asarray(xy, float) - [c["cx"], c["cy"]]
    return np.c_[d[:, 0] * np.cos(t) + d[:, 1] * np.sin(t), -d[:, 0] * np.sin(t) + d[:, 1] * np.cos(t)]


def to_icon(c, lx, ly):
    t = np.radians(c["ang"])
    return np.array([c["cx"] + lx * np.cos(t) - ly * np.sin(t), c["cy"] + lx * np.sin(t) + ly * np.cos(t)])


def rect_outline(c, n=60):
    pts = []
    hw, hh, r = c["w"] / 2, c["h"] / 2, c["r"]
    for cx, cy, a0 in ((hw - r, -hh + r, -90), (hw - r, hh - r, 0), (-hw + r, hh - r, 90), (-hw + r, -hh + r, 180)):
        for a in np.linspace(a0, a0 + 90, n):
            pts.append(to_icon(c, cx + r * np.cos(np.radians(a)), cy + r * np.sin(np.radians(a))))
    return np.array(pts)


def quad_outline(q, r=CORNER_R, n=30):
    """Closed outline of quadrilateral q (TL, TR, BR, BL) with fillets of radius ~r."""
    out = []
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        u1, u2 = (a - b) / np.linalg.norm(a - b), (c - b) / np.linalg.norm(c - b)
        t = r / np.tan(np.arccos(np.clip(u1 @ u2, -1, 1)) / 2)
        p0, p2 = b + u1 * t, b + u2 * t
        for s in np.linspace(0, 1, n):
            out.append((1 - s) ** 2 * p0 + 2 * (1 - s) * s * b + s ** 2 * p2)
    return np.array(out)


# ---------------------------------------------------------------- trace analysis

def thin(mask):
    """Zhang-Suen thinning to a 1 px centre line."""
    img = mask.astype(np.uint8).copy()
    while True:
        changed = False
        for step in (0, 1):
            P = np.pad(img, 1)
            p = [P[1 + dy:P.shape[0] - 1 + dy, 1 + dx:P.shape[1] - 1 + dx] for dy, dx in NB]
            p2, p3, p4, p5, p6, p7, p8, p9 = p
            B = sum(x.astype(int) for x in p)
            seq = p + [p2]
            A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(int) for i in range(8))
            c = ((p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)) if step == 0 else \
                ((p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0))
            m = (img == 1) & (B >= 2) & (B <= 6) & (A == 1) & c
            if m.any():
                img[m] = 0
                changed = True
        if not changed:
            return img.astype(bool)


def enclosed_holes(mask, max_area):
    """Small background regions fully enclosed by the drawing: (cx, cy) centroids."""
    inv = ~mask
    h, w = mask.shape
    seen = np.zeros(mask.shape, bool)
    out = []
    for y0, x0 in zip(*np.nonzero(inv)):
        if seen[y0, x0]:
            continue
        q, pts, edge = deque([(y0, x0)]), [], False
        seen[y0, x0] = True
        while q:
            y, x = q.popleft()
            pts.append((x, y))
            edge |= y in (0, h - 1) or x in (0, w - 1)
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and inv[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if not edge and len(pts) <= max_area:
            out.append(np.mean(pts, axis=0))
    return out


def snap(curve, skel, radius=22):
    out = curve.copy()
    for i, p in enumerate(curve):
        d = np.hypot(*(skel - p).T)
        j = d.argmin()
        if d[j] < radius:
            out[i] = skel[j]
    return out


def fit_card_rect(skel, exclude):
    """Rounded rectangle (fixed corner radius) fitted to card-edge centre-line pixels."""
    c = dict(CARD0)
    pts = skel[~exclude]
    best = None
    for _ in range(3):
        near = pts[nearest_dist(pts, rect_outline(c)) < 18]

        def cost(cc):
            return np.mean(nearest_dist(near, rect_outline(cc, 30)) ** 2)
        steps = dict(cx=2, cy=2, w=2, h=2, ang=0.3)
        best = cost(c)
        while max(steps.values()) > 0.05:
            improved = False
            for k, s in steps.items():
                for sgn in (1, -1):
                    cc = dict(c)
                    cc[k] += sgn * s
                    v = cost(cc)
                    if v < best:
                        best, c, improved = v, cc, True
            if not improved:
                steps = {k: v / 2 for k, v in steps.items()}
    return c


def fit_card_sides(rect, skel):
    """Each visible side as its own line, using only the stretch nothing else
    touches; corners where neighbouring sides meet. Returns TL, TR, BR, BL."""
    lx, ly = to_local(rect, skel).T
    hw, hh = rect["w"] / 2, rect["h"] / 2
    sides = {
        "top": (np.abs(ly + hh) < 25) & (np.abs(lx) < hw - 40),
        "left": (np.abs(lx + hw) < 25) & (ly > -hh + 40) & (ly < -60),        # above the fingers
        "right": (np.abs(lx - hw) < 25) & (ly > -hh + 40) & (ly < -70),       # above the thumb
        "bottom": (np.abs(ly - hh) < 25) & (lx > -hw + 10) & (lx < -10),      # fingers .. wrist
    }
    lines = {}
    for k, sel in sides.items():
        u, v = (lx[sel], ly[sel]) if k in ("top", "bottom") else (ly[sel], lx[sel])
        for _ in range(2):  # fit, drop > 6 px outliers, refit
            a, b = np.polyfit(u, v, 1)
            ok = np.abs(v - (a * u + b)) < 6
            u, v = u[ok], v[ok]
        lines[k] = (a, b)

    def corner(h, v):
        (a1, b1), (a2, b2) = lines[h], lines[v]  # ly = a1*lx + b1 ; lx = a2*ly + b2
        y = (a1 * b2 + b1) / (1 - a1 * a2)
        return to_icon(rect, a2 * y + b2, y)
    return np.array([corner("top", "left"), corner("top", "right"),
                     corner("bottom", "right"), corner("bottom", "left")])


# ---------------------------------------------------------------- the icon

def fit(trace_mask):
    """Fit the stroke model to a trace mask (in the trace frame). Returns a dict."""
    m = trace_mask[OFF:OFF + FRAME[1], OFF:OFF + FRAME[0]]
    ys, xs = np.nonzero(thin(m))
    skel = np.c_[xs, ys].astype(float)

    def template(pts):
        return resample(catmull_rom(pts), 4.0)
    back, wrist, thumb = (smooth(snap(template(t), skel)) for t in (BACK0, WRIST0, THUMB0))

    loc = to_local(CARD0, skel)
    finger_zone = (loc[:, 0] < -CARD0["w"] / 2 + 40) & (loc[:, 1] > -60) & (loc[:, 1] < CARD0["h"] / 2 - 20)
    near_hand = nearest_dist(skel, np.r_[back, wrist, thumb]) < 26
    card = fit_card_sides(fit_card_rect(skel, finger_zone | near_hand), skel)

    # Re-snap the hand curves to centre-line pixels that are not the card edge,
    # so they do not get pulled onto it where they cross it (that put a kink in
    # the top of the thumb).
    # Only nearby matches are taken; across the crossing itself the samples keep
    # their template position and the smoothing bridges them.
    hand_skel = skel[nearest_dist(skel, quad_outline(card)) > 12]
    back, wrist = (smooth(snap(template(t), hand_skel)) for t in (BACK0, WRIST0))
    thumb = snap(template(THUMB0), hand_skel, radius=12)
    # The thumb's top edge (web -> tip) is one smooth curve: fit a cubic to the
    # snapped samples, skipping those at the card-edge crossing, where the
    # centre line is ambiguous (that left a kink).
    tip = int(np.argmin(thumb[:, 0]))
    top = thumb[:tip + 1]
    t = np.r_[0, np.cumsum(np.hypot(*np.diff(top, axis=0).T))]
    clear = nearest_dist(top, quad_outline(card)) > 20
    fx, fy = (np.polyfit(t[clear], top[clear, k], 3) for k in (0, 1))
    end = max(0, tip - 6)                    # leave the tip itself to the trace
    thumb[:end] = np.c_[np.polyval(fx, t[:end]), np.polyval(fy, t[:end])]
    thumb = smooth(thumb, sigma=THUMB_SIGMA)

    # fingertips, in the frame of the card's left edge (TL -> BL, outward normal)
    tl, bl = card[0], card[3]
    ue = (bl - tl) / np.linalg.norm(bl - tl)
    ne = np.array([ue[1], -ue[0]])
    if ne @ (card.mean(0) - tl) > 0:
        ne = -ne

    def edge_local(xy):
        d = np.asarray(xy, float) - tl
        return np.c_[d @ ue, d @ ne]
    holes = [h for h in enclosed_holes(m, 400) if edge_local([h])[0, 1] > -30]
    along = np.sort(edge_local(sorted(holes, key=lambda h: h[1])[:3])[:, 0])
    spacing = float(np.mean(np.diff(along)))
    mys, mxs = np.nonzero(m)
    ml = edge_local(np.c_[mxs, mys])
    # each fingertip's own reach (they differ: middle furthest, then top, bottom least)
    reach = [ml[(np.abs(ml[:, 0] - a) < 6) & (ml[:, 1] > -10), 1].max() + REACH_ADJ for a in along]
    half = LINE_WIDTH / 2
    fingers = [dict(a=a, c=half, rx=r_ - half, ry=spacing / 2) for a, r_ in zip(along, reach)]
    model = dict(card=card, back=back, wrist=wrist, thumb=thumb, edge=(tl, ue, ne),
                 fingers=fingers, tilt=0.0)
    fit_fingers(model, m, along, ml)
    return model


def fit_fingers(model, m, along, ml):
    """Refine the fingertip loops (shared tilt and height, each one's centre and
    reach) to minimise the mismatch with the trace around the fingers."""
    region = (ml[:, 0] > along[0] - 45) & (ml[:, 0] < along[-1] + 45) & (ml[:, 1] > -20) & (ml[:, 1] < 110)
    ys, xs = np.nonzero(m)
    # region as a pixel mask (covering background pixels too)
    tl, ue, ne = model["edge"]
    gy, gx = np.mgrid[0:m.shape[0], 0:m.shape[1]]
    d = np.c_[gx.ravel(), gy.ravel()] - tl
    la, lo = (d @ ue).reshape(m.shape), (d @ ne).reshape(m.shape)
    roi = (la > along[0] - 45) & (la < along[-1] + 45) & (lo > -20) & (lo < 110)

    def cost():
        g = np.asarray(render(model, ss=1)) > 128
        return int((g ^ m)[roi].sum())

    knobs = [("tilt", None, 4.0)] + [("ry", i, 3.0) for i in range(3)] + \
            [(k, i, 3.0) for i in range(3) for k in ("c", "rx")]
    best = cost()
    steps = {(k, i): s for k, i, s in knobs}
    while max(steps.values()) > 0.25:
        improved = False
        for (k, i), st in list(steps.items()):
            for sgn in (1, -1):
                tgt = model if i is None else model["fingers"][i]
                old = tgt[k]
                tgt[k] = old + sgn * st
                v = cost()
                if v < best:
                    best, improved = v, True
                    break
                tgt[k] = old
        if not improved:
            steps = {k: v / 2 for k, v in steps.items()}
    model["finger_fit_xor"] = best


def render(model, width=LINE_WIDTH, ss=3):
    """Draw the fitted model in the reference frame, antialiased (L image)."""
    w, h = FRAME
    im = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(im)
    lw = width * ss
    S = lambda pts: [(x * ss, y * ss) for x, y in pts]  # noqa: E731

    def stroke(pts):
        pts = S(pts)
        d.line(pts, fill=255, width=int(round(lw)))
        for x, y in pts:  # round joins and caps
            d.ellipse([x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2], fill=255)

    # 1. back of hand and wrist (both start behind the card)
    stroke(model["back"])
    stroke(model["wrist"])
    # 2. fingertips: D loops on the outer side of the card's left edge, touching
    tl, ue, ne = model["edge"]
    half = width / 2
    th = np.radians(model["tilt"])            # loops lean toward the top of the card
    out_dir = ne * np.cos(th) - ue * np.sin(th)
    along_dir = ue * np.cos(th) + ne * np.sin(th)
    for f in model["fingers"]:
        centre = tl + ue * f["a"] + ne * f["c"]

        def ellipse(rx, ry):
            return [centre + out_dir * rx * np.cos(t) + along_dir * ry * np.sin(t)
                    for t in np.linspace(0, 2 * np.pi, 90)]
        d.polygon(S(ellipse(f["rx"], f["ry"])), fill=255)
        d.polygon(S(ellipse(f["rx"] - width, f["ry"] - width)), fill=0)
    # 3. the card, in front of the hand lines
    outline = quad_outline(model["card"])
    d.polygon(S(outline), fill=0)
    stroke(np.r_[outline, outline[:1]])
    # 4. the thumb, in front of the card: hide the card edge under it, outline it
    thumb = model["thumb"]
    a0, a1 = thumb[0], thumb[-1]
    d.polygon(S(list(thumb) + [a1 + (26, 0), a0 + (26, 0)]), fill=0)
    back = model["back"]
    stroke(back[np.hypot(*(back - a0).T) < 60])  # where the fill clipped the back line
    stroke(thumb)
    return im.resize((w, h), Image.LANCZOS)
