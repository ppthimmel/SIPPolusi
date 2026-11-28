"""Turn the logo artwork (off-white background) into transparent PNGs for the frontend.

Usage: python scripts/build_frontend_logo.py src/frontend/public/logo-source.jpg src/frontend/public
"""
import sys
from pathlib import Path

from PIL import Image

src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)
img = Image.open(src).convert("RGB")
w, h = img.size

# Background colour: median of the four corners.
corners = [img.getpixel(p) for p in [(5, 5), (w - 6, 5), (5, h - 6), (w - 6, h - 6)]]
bg = tuple(sorted(c[i] for c in corners)[len(corners) // 2] for i in range(3))

# Alpha from the distance to the background: 0 at the background, 255 from ~22 away (the pale cloud is ~40 away and must stay solid), a soft
# ramp in between so anti-aliased edges stay smooth. Colour is un-blended from the background.
px = img.load()
rgba = Image.new("RGBA", (w, h))
out = rgba.load()
for y in range(h):
    for x in range(w):
        r, g, b = px[x, y]
        d = max(abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2]))
        a = 0 if d <= 6 else 255 if d >= 22 else int((d - 6) / 16 * 255)
        if a == 0:
            out[x, y] = (0, 0, 0, 0)
        else:
            k = a / 255
            un = tuple(max(0, min(255, round((c - (1 - k) * bc) / k))) for c, bc in zip((r, g, b), bg))
            out[x, y] = (*un, a)

# Trim to the drawing, then pad to a square with a little margin.
bbox = rgba.getbbox()
art = rgba.crop(bbox)
side = int(max(art.size) * 1.08)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(art, ((side - art.width) // 2, (side - art.height) // 2), art)

square.resize((512, 512), Image.LANCZOS).save(out_dir / "logo.png", optimize=True)
square.resize((192, 192), Image.LANCZOS).save(out_dir / "logo-192.png", optimize=True)
square.resize((64, 64), Image.LANCZOS).save(out_dir / "favicon.png", optimize=True)
# Original artwork kept as delivered, for print and future edits.
img.save(out_dir / "logo-source.jpg", quality=95)
print("background", bg, "bbox", bbox, "->", [p.name for p in sorted(out_dir.iterdir())])
