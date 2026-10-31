#!/usr/bin/env python3
"""LED-look PNG/GIF mockups from the simulator's frames (tools/sim/sim.cpp runs the firmware's
page engine on a PC, so these show exactly what the firmware draws on one 128x32 face).

  tools/mockups.sh OUTDIR          # builds the simulator, runs it, then this script
  python3 tools/mockups.py FRAMEDIR OUTDIR
"""
import os
import sys
from PIL import Image, ImageDraw, ImageFilter

AMBER = (255, 150, 0)      # LED colour used by the firmware
OFF = (34, 24, 18)         # unlit LED
BG = (8, 8, 8)
W, H = 128, 32

CAPTIONS = {
    "01_train_list": "Train list (X6, Sep 23 2024 rows)",
    "02_train_list_widest": "Train list, widest fields: 2-digit hour + '45 MIN LATE' / 'CANCELLED'",
    "03_clock": "Clock page",
    "04_attention": "ATTENTION / #NNN APPROACHING (10 s)",
    "05_arriving_on": "Train list after the approach page: ARRIVING (on phase)",
    "06_arriving_off": "ARRIVING (off phase; 1.0 s period, 0.47 s on)",
    "07_scroll_t2s": "Scrolling alert, 2 s after start (enters from an empty panel)",
    "08_scroll_t6s": "Scrolling alert, 6 s",
    "09_scroll_t16s": "Scrolling alert, 16 s (cut at 16.6 s)",
}


def read_pbm(path):
    d = open(path, "rb").read()
    return d[d.index(b"\n", 3) + 1:]


def read_anim(path):
    out = []
    for l in open(path).read().split("\n")[1:]:
        if l:
            ms, hx = l.split()
            out.append((int(ms), bytes.fromhex(hx)))
    return out


def led_image(bits, s=8):
    """bits: 512 bytes, row-major MSB first. s = pixels per LED."""
    m = s
    im = Image.new("RGB", (W * s + 2 * m, H * s + 2 * m), BG)
    lit = Image.new("L", im.size, 0)
    d, dl = ImageDraw.Draw(im), ImageDraw.Draw(lit)
    r = s * 0.36
    for y in range(H):
        for x in range(W):
            on = bits[y * 16 + x // 8] & (0x80 >> (x % 8))
            cx, cy = m + x * s + s / 2, m + y * s + s / 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=AMBER if on else OFF)
            if on:
                dl.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    halo = lit.filter(ImageFilter.GaussianBlur(s * 0.6)).point(lambda v: int(v * 0.35))
    im = Image.composite(Image.new("RGB", im.size, AMBER), im, halo)
    core = lit.filter(ImageFilter.GaussianBlur(s * 0.08)).point(lambda v: int(v * 0.25))
    return Image.composite(Image.new("RGB", im.size, (255, 200, 90)), im, core)


def caption(im, text):
    out = Image.new("RGB", (im.width, im.height + 34), (24, 24, 24))
    out.paste(im, (0, 0))
    ImageDraw.Draw(out).text((10, im.height + 9), text, fill=(220, 220, 220))
    return out


def save_gif(frames, path, s):
    """frames: [(hold_ms, bits)]; GIF delays are 10 ms ticks, so carry the rounding error."""
    ims, durs, t, ticks, cache = [], [], 0.0, 0, {}
    for ms, bits in frames:
        t += ms
        new = int(round(t / 10))
        if new <= ticks:
            continue
        if bits not in cache:
            cache[bits] = led_image(bits, s).convert("P", palette=Image.ADAPTIVE, colors=16)
        ims.append(cache[bits])
        durs.append((new - ticks) * 10)
        ticks = new
    ims[0].save(path, save_all=True, append_images=ims[1:], duration=durs, loop=0)
    print(f"{path}: {len(ims)} frames, {sum(durs) / 1000:.2f} s, {os.path.getsize(path) // 1024} KiB")


def main(src, out):
    os.makedirs(out, exist_ok=True)
    sheet = []
    for name in sorted(f[:-4] for f in os.listdir(src) if f.endswith(".pbm")):
        im = led_image(read_pbm(os.path.join(src, name + ".pbm")))
        im.save(os.path.join(out, name + ".png"))
        sheet.append(caption(im, f"{name}: {CAPTIONS.get(name, '')}"))
        print(os.path.join(out, name + ".png"))
    allim = Image.new("RGB", (sheet[0].width, sum(i.height for i in sheet)), (24, 24, 24))
    y = 0
    for i in sheet:
        allim.paste(i, (0, y))
        y += i.height
    allim.save(os.path.join(out, "_all_pages.png"))
    a = lambda n: read_anim(os.path.join(src, n + ".anim"))
    save_gif(a("anim_scroll"), os.path.join(out, "scroll.gif"), 4)
    save_gif(a("anim_arriving_blink"), os.path.join(out, "arriving_blink.gif"), 5)
    save_gif(a("anim_approach_a") + a("anim_approach_b"), os.path.join(out, "approach_sequence.gif"), 5)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
