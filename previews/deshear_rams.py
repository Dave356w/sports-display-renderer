"""Make the original RAMS lettering in text-only-pennants.png upright.

Keeps the sheet's own RAMS letterforms: the italic yellow letters inside the
LAR pennant's blue field are lifted out, each pixel row is shifted
horizontally to undo the ~19.7 degree oblique (a pure shear, so no resampling
and no new colours), and the result is pasted back on a clean blue field.
Usage: deshear_rams.py ORIGINAL_SHEET OUT_SHEET [SHIFT_X SHIFT_Y]
"""
import sys
import numpy as np
from PIL import Image

SRC, OUT = sys.argv[1], sys.argv[2]
SHIFT_X = int(sys.argv[3]) if len(sys.argv) > 3 else 0
SHIFT_Y = int(sys.argv[4]) if len(sys.argv) > 4 else 0
BOX = (27, 809, 1004, 1146)                 # LAR crop used by load_pennant()
BLUE, YELLOW = (0, 0, 255), (255, 255, 0)

sheet = Image.open(SRC).convert("RGB")
a = np.array(sheet.crop(BOX))
yellow = (a[..., 0] == 255) & (a[..., 1] == 255) & (a[..., 2] == 0)
blue = (a[..., 0] == 0) & (a[..., 1] == 0) & (a[..., 2] == 255)
h, w = blue.shape
top = np.array([np.flatnonzero(blue[:, x]).min() if blue[:, x].any() else -1 for x in range(w)])
bot = np.array([np.flatnonzero(blue[:, x]).max() if blue[:, x].any() else -1 for x in range(w)])

letters = np.zeros_like(yellow)
for x in range(w):
    if top[x] >= 0:
        letters[top[x]:bot[x] + 1, x] = yellow[top[x]:bot[x] + 1, x]
        a[top[x]:bot[x] + 1, x] = BLUE       # clean field

# Shear from the R stem's left edge: x drifts left as y increases.
ys = np.arange(100, 250)
edge = np.array([np.flatnonzero(letters[y, :200]).min() for y in ys])
k = -np.polyfit(ys, edge, 1)[0]
rows = np.flatnonzero(letters.any(axis=1))
pivot = (rows.min() + rows.max()) / 2    # letters stay centred where they were
upright = np.zeros_like(letters)
for y in rows:
    dx = int(round(-k * (pivot - y))) + SHIFT_X
    src = letters[y]
    if dx >= 0:
        upright[y, dx:] = src[:w - dx]
    else:
        upright[y, :w + dx] = src[-dx:]
upright = np.roll(upright, SHIFT_Y, axis=0)   # rows near the edges are empty
print(f"shear k={k:.4f} ({np.degrees(np.arctan(k)):.1f} deg), pivot y={pivot:.0f}")

# Clearance from the field's angled edges and the hoist.
ly, lx = np.nonzero(upright)
inside = (top[lx] >= 0) & (ly >= top[lx]) & (ly <= bot[lx])
assert inside.all(), "letters left the blue field"
print("min clearance top", (ly - top[lx]).min(), "bottom", (bot[lx] - ly).min(),
      "bbox", lx.min(), ly.min(), lx.max(), ly.max())

a[upright] = YELLOW
sheet.paste(Image.fromarray(a), BOX[:2])
sheet.save(OUT, format="PNG", optimize=True)
