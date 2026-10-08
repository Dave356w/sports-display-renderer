"""Redraw the RAMS lettering in assets/nfl/text-only-pennants.png upright.

Only pixels strictly inside the blue field of the LAR pennant are touched:
the italic yellow letters are filled back to blue, then upright slab-serif
letters are drawn in pure yellow, each centred on the pennant's horizontal
centreline and sized to the local height of the tapering field.
"""
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SHEET, OUT = sys.argv[1], sys.argv[2]
BOX = (27, 809, 1004, 1146)          # LAR crop used by load_pennant()
BLUE, YELLOW = (0, 0, 255), (255, 255, 0)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
BOLDEN = 9                            # square dilation: heavier stems, crisp slab corners
CONDENSE = 0.64                       # narrow the face toward the other pennants' block letters
MARGIN = 22                           # clearance from the angled yellow borders
TRACK = 12                            # gap between letters
START_X = 40                          # left margin inside the hoist border
STEPS = (1.0, 0.88, 0.77, 0.67)          # gentle step-down toward the tip, as on the other pennants

sheet = Image.open(SHEET).convert("RGB")
region = sheet.crop(BOX)
a = np.array(region)
blue = (a[..., 0] == 0) & (a[..., 1] == 0) & (a[..., 2] == 255)

top = np.full(a.shape[1], -1); bot = np.full(a.shape[1], -1)
for x in range(a.shape[1]):
    rows = np.where(blue[:, x])[0]
    if len(rows):
        top[x], bot[x] = rows.min(), rows.max()

# Erase the italic letters: everything between the field's top and bottom edge
# in each column becomes blue again (the letters never touch the border).
for x in range(a.shape[1]):
    if top[x] >= 0:
        a[top[x]:bot[x] + 1, x] = BLUE
field = Image.fromarray(a)

def interior(x0, x1):
    xs = range(max(x0, 0), min(x1, a.shape[1]))
    t = max(top[x] for x in xs); b = min(bot[x] for x in xs)
    return t + MARGIN, b - MARGIN

def glyph_mask(ch, size):
    font = ImageFont.truetype(FONT, size)
    l, t, r, b = font.getbbox(ch, anchor="ls")
    pad = BOLDEN
    m = Image.new("L", (r - l + 2 * pad, b - t + 2 * pad), 0)
    ImageDraw.Draw(m).text((pad - l, pad - t), ch, font=font, fill=255, anchor="ls")
    m = m.resize((max(1, round(m.width * CONDENSE)), m.height), Image.Resampling.LANCZOS)
    m = m.point(lambda v: 255 if v >= 128 else 0).filter(ImageFilter.MaxFilter(BOLDEN))
    return m.crop(m.getbbox())

def layout(size):
    """Place each letter for a base font size; None if any letter misses the field."""
    placed, x = [], START_X
    for ch, step in zip("RAMS", STEPS):
        m = glyph_mask(ch, round(size * step))
        t, b = interior(x, x + m.width)
        if m.height > b - t:
            return None
        centre = (top[x:x + m.width].mean() + bot[x:x + m.width].mean()) / 2
        y = min(max(int(round(centre - m.height / 2)), t), b - m.height)
        placed.append((m, x, y))
        x += m.width + TRACK
    return placed

for size in range(400, 40, -2):
    placed = layout(size)
    if placed:
        break
for m, x, y in placed:
    field.paste(YELLOW, (x, y), m)
    print("letter", x, y, m.size)

sheet.paste(field, BOX[:2])
sheet.save(OUT, format="PNG", optimize=True)
