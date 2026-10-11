#!/usr/bin/env python3
"""
Overlay the generated hand icon on its reference (assets/ref_hand_icon_sfcta.png).

    python3 tools/compare_icon.py [out_dir]     (default: build/icon_compare)

Writes side_by_side.png, overlay.png (reference in red, generated icon in
green, yellow where they agree) and actual_size.png (as it will appear on the panel).
"""
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_assets  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "icon_compare")
os.makedirs(out, exist_ok=True)

ref = Image.open(os.path.join(ROOT, "assets", "ref_hand_icon_sfcta.png")).convert("L")
mine = gen_assets.draw_tap_icon(crop=False)
assert ref.size == mine.size, (ref.size, mine.size)

# binarise the photo so the overlay shows line positions, not JPEG noise
r = (np.asarray(ref) > 128).astype(np.uint8) * 255
g = np.asarray(mine)
Image.fromarray(np.dstack([r, g, np.zeros_like(r)])).save(os.path.join(out, "overlay.png"))

w, h = ref.size
side = Image.new("L", (w * 2 + 20, h), 60)
side.paste(ref, (0, 0))
side.paste(mine, (w + 20, 0))
side.save(os.path.join(out, "side_by_side.png"))

# what it will look like on the panel: actual pixel size on the idle navy
icon = gen_assets.draw_tap_icon()
panel = Image.new("RGB", (icon.width + 80, icon.height + 80), (0x12, 0x1A, 0x30))
panel.paste(Image.new("RGB", icon.size, (255, 255, 255)), (40, 40), icon)
panel.save(os.path.join(out, "actual_size.png"))
print("wrote", out)
