"""
Run once (via GitHub Actions) to regenerate assets/background.png.
Produces a clean, ePaper-optimised 960x1600 canvas:
  - no frame border
  - no static date (drawn dynamically by render.py)
  - no GB legend
  - space at top for a logo to be added later
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 960, 1600
OUT  = Path(__file__).parent / "assets" / "background.png"

BG      = (255, 255, 255)   # white — maps cleanly to ePaper base colour
NAVY    = (8,  42,  78)
DIVIDER = (200, 200, 200)

FONT_HEAVY = "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf"
FALLBACK   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

SLOT_Y = [370, 600, 830, 1060, 1290]   # must match render.py


def fnt(size):
    p = Path(FONT_HEAVY)
    return ImageFont.truetype(str(p) if p.exists() else FALLBACK, size)


img  = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)

# ── Title ────────────────────────────────────────────────────────────────────
draw.text((W // 2, 130), "NL WEST",   font=fnt(80), fill=NAVY, anchor="mm")
draw.text((W // 2, 235), "STANDINGS", font=fnt(80), fill=NAVY, anchor="mm")

# ── Column headers (date drawn dynamically at y≈310) ─────────────────────────
draw.text((710, 350), "W-L", font=fnt(38), fill=NAVY, anchor="mm")
draw.text((840, 350), "GB",  font=fnt(38), fill=NAVY, anchor="mm")

# ── Divider below headers ────────────────────────────────────────────────────
draw.line([(40, 365), (W - 40, 365)], fill=DIVIDER, width=2)

# ── Dividers between team rows ───────────────────────────────────────────────
for y in SLOT_Y[1:]:
    draw.line([(40, y - 4), (W - 40, y - 4)], fill=DIVIDER, width=2)

# ── Bottom divider ───────────────────────────────────────────────────────────
draw.line([(40, SLOT_Y[-1] + 180), (W - 40, SLOT_Y[-1] + 180)], fill=DIVIDER, width=2)

OUT.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT, "PNG")
print(f"Wrote {OUT}")
