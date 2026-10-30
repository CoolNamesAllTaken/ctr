#!/usr/bin/env python3
"""
Generate LVGL image assets (A8 alpha masks, recoloured at draw time) into src/assets/.

  chipper_mark      the stacked-drops logo mark, cut out of assets/chipper_card_art.png
  chipper_wordmark  the "CHIPPER(R)" wordmark from the same artwork
  icon_tap          hand-holding-a-card icon: strokes fitted to the reference photo
                    (tools/hand_icon.py)
  icon_check/cross  result glyphs

Run from the project root:  python3 tools/gen_assets.py   (needs Pillow + numpy)
"""
import os
from collections import deque

import numpy as np
from PIL import Image, ImageDraw

import hand_icon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "assets", "chipper_card_art.png")
OUT = os.path.join(ROOT, "src", "assets")

MARK_HEIGHT = 150      # px on screen
WORDMARK_HEIGHT = 42
ICON_HEIGHT = 190
GLYPH_SIZE = 140
SS = 4                 # supersampling for the glyphs


def alpha_from_art(img: Image.Image) -> Image.Image:
    """White artwork on a blue gradient -> alpha. The red channel separates them cleanly."""
    r = np.asarray(img.convert("RGB"))[:, :, 0].astype(np.float32)
    a = np.clip((r - 60.0) / (235.0 - 60.0), 0.0, 1.0)
    return Image.fromarray((a * 255).astype(np.uint8))


def crop_rows(alpha: Image.Image, y0: int, y1: int) -> Image.Image:
    band = alpha.crop((0, y0, alpha.width, y1))
    return band.crop(band.getbbox())


def scale_to_height(img: Image.Image, h: int) -> Image.Image:
    w = round(img.width * h / img.height)
    return img.resize((w, h), Image.LANCZOS)


def white_row_bands(alpha: Image.Image):
    rows = np.where((np.asarray(alpha) > 128).any(axis=1))[0]
    bands, start, prev = [], rows[0], rows[0]
    for r in rows[1:]:
        if r != prev + 1:
            bands.append((start, prev + 1))
            start = r
        prev = r
    bands.append((start, prev + 1))
    return bands


# ---------------------------------------------------------------- icons

def _gauss(img: np.ndarray, sigma: float) -> np.ndarray:
    r = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(img, r, mode="edge")
    tmp = np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 1, pad)
    return np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 0, tmp)


def _drop_specks(mask: np.ndarray, min_frac: float = 0.05) -> np.ndarray:
    """Remove connected blobs smaller than min_frac of the largest (dust on the screen)."""
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    sizes = [0]
    for y0, x0 in zip(*np.nonzero(mask)):
        if lab[y0, x0]:
            continue
        n = len(sizes)
        lab[y0, x0] = n
        q, c = deque([(y0, x0)]), 0
        while q:
            y, x = q.popleft()
            c += 1
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = n
                    q.append((ny, nx))
        sizes.append(c)
    sizes = np.array(sizes)
    keep = sizes >= sizes.max() * min_frac
    keep[0] = False
    return keep[lab]


def trace_tap_icon(scale: int, blur_src_px: float = 0.65) -> np.ndarray:
    """
    Trace the validator's hand-holding-a-card icon from the photo, as a binary
    mask at `scale` x the source resolution.

    Source: assets/ref_hand_icon_source.png, an unmodified 120x127 crop of the
    full-resolution SFCTA photo
    https://www.sfcta.org/sites/default/files/2024-07/229027_LRV4_Interior_7.jpg
    (icon starts 5 px in). Normalise to the screen's black/white levels,
    upsample, smooth away sensor/JPEG noise, threshold at half-maximum (the
    true edge of a blurred line), and drop dust specks.
    """
    src = Image.open(os.path.join(ROOT, "assets", "ref_hand_icon_source.png")).convert("L")
    a = np.asarray(src, dtype=np.float32)
    bg, fg = np.percentile(a, 20), np.percentile(a, 99.5)
    n = np.clip((a - bg) / (fg - bg), 0, 1)
    up = np.asarray(Image.fromarray(n).resize((src.width * scale, src.height * scale), Image.BICUBIC))
    return _drop_specks(_gauss(up, blur_src_px * scale) > 0.5)


def draw_tap_icon(crop: bool = True) -> Image.Image:
    """
    The hand-with-card icon as smooth strokes (tools/hand_icon.py), each part
    fitted to the trace of the reference photo.
    crop=False: the 660x702 reference frame, for tools/compare_icon.py.
    """
    model = hand_icon.fit(trace_tap_icon(hand_icon.SCALE))
    im = hand_icon.render(model)
    if not crop:
        return im
    im = im.crop(im.getbbox())
    h = ICON_HEIGHT
    return im.resize((round(im.width * h / im.height), h), Image.LANCZOS)


def draw_glyph(kind: str) -> Image.Image:
    N = GLYPH_SIZE * SS
    im = Image.new("L", (N, N), 0)
    d = ImageDraw.Draw(im)
    st = int(N * 0.075)
    d.ellipse([st / 2, st / 2, N - st / 2, N - st / 2], outline=255, width=st)
    lw = int(N * 0.09)
    if kind == "check":
        pts = [(0.28, 0.52), (0.44, 0.68), (0.73, 0.36)]
        d.line([(x * N, y * N) for x, y in pts], fill=255, width=lw, joint="curve")
        ends = [pts[0], pts[-1]]
    elif kind == "prohibited":
        # circle with a diagonal bar from upper-left to lower-right
        import math
        r = N / 2 - st / 2
        c = N / 2
        k = math.cos(math.radians(45)) * r
        d.line([(c - k, c - k), (c + k, c + k)], fill=255, width=st)
        ends = []
    else:
        a, b = 0.33, 0.67
        d.line([(a * N, a * N), (b * N, b * N)], fill=255, width=lw)
        d.line([(b * N, a * N), (a * N, b * N)], fill=255, width=lw)
        ends = [(a, a), (b, b), (b, a), (a, b)]
    for x, y in ends:  # round caps
        d.ellipse([x * N - lw / 2, y * N - lw / 2, x * N + lw / 2, y * N + lw / 2], fill=255)
    return im.resize((GLYPH_SIZE, GLYPH_SIZE), Image.LANCZOS)


# ---------------------------------------------------------------- output

def write_a8(name: str, img: Image.Image):
    data = np.asarray(img, dtype=np.uint8)
    h, w = data.shape
    body = ",\n".join(
        "    " + ", ".join(f"0x{v:02x}" for v in data.flatten()[i:i + 24])
        for i in range(0, w * h, 24)
    )
    src = f"""/* Generated by tools/gen_assets.py - do not edit. */
#include "lvgl.h"

static const uint8_t {name}_map[] = {{
{body}
}};

const lv_image_dsc_t {name} = {{
    .header = {{
        .magic = LV_IMAGE_HEADER_MAGIC,
        .cf = LV_COLOR_FORMAT_A8,
        .w = {w},
        .h = {h},
        .stride = {w},
    }},
    .data_size = sizeof({name}_map),
    .data = {name}_map,
}};
"""
    with open(os.path.join(OUT, f"{name}.c"), "w") as f:
        f.write(src)
    img.save(os.path.join(OUT, f"{name}.preview.png"))
    print(f"{name}: {w}x{h}")
    return name


def main():
    os.makedirs(OUT, exist_ok=True)
    alpha = alpha_from_art(Image.open(ART))
    bands = white_row_bands(alpha)
    # Every band but the last is part of the drops; the last is the wordmark.
    mark = crop_rows(alpha, bands[0][0], bands[-2][1])
    word = crop_rows(alpha, bands[-1][0], bands[-1][1])

    names = [
        write_a8("img_chipper_mark", scale_to_height(mark, MARK_HEIGHT)),
        write_a8("img_chipper_wordmark", scale_to_height(word, WORDMARK_HEIGHT)),
        write_a8("img_icon_tap", draw_tap_icon()),
        write_a8("img_icon_check", draw_glyph("check")),
        write_a8("img_icon_cross", draw_glyph("cross")),
        write_a8("img_icon_prohibited", draw_glyph("prohibited")),
    ]
    with open(os.path.join(OUT, "assets.h"), "w") as f:
        f.write("/* Generated by tools/gen_assets.py - do not edit. */\n#pragma once\n\n#include \"lvgl.h\"\n\n")
        for n in names:
            f.write(f"LV_IMAGE_DECLARE({n});\n")


if __name__ == "__main__":
    main()
