#!/usr/bin/env python3
"""
Render the NL West standings scoreboard poster.

Expected folder layout:
render.py
public/
  mlb_nl_west.png
assets/
  background.png
  pennants/
    LAD.png
    SD.png
    ARI.png
    COL.png
    SF.png

Dependency:
  pip install pillow
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import os

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
PENNANTS = ASSETS / "pennants"
PUBLIC = ROOT / "public"
OUTPUT = PUBLIC / "mlb_nl_west.png"

CANVAS_SIZE = (1228, 2048)
NAVY = (7, 35, 65)

STANDINGS = [
    {"abbr": "LAD", "record": "31-16", "gb": "—",   "y": 580},
    {"abbr": "SD",  "record": "28-19", "gb": "3.0", "y": 868},
    {"abbr": "ARI", "record": "24-23", "gb": "7.0", "y": 1156},
    {"abbr": "COL", "record": "16-29", "gb": "14.0", "y": 1444},
    {"abbr": "SF",  "record": "15-30", "gb": "15.0", "y": 1732},
]

# Render placement. Adjust these if you want to adapt this to another screen ratio.
PENNANT_X = 84
PENNANT_W = 760
PENNANT_H = 236
RECORD_X = 930
GB_X = 1102

FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansDisplay-CondensedBlack.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/Library/Fonts/Arial Narrow Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()

def draw_text_centered(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font, fill=NAVY) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((x - tw / 2, y - th / 2), text, font=font, fill=fill)

def render() -> Path:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    poster = Image.open(ASSETS / "background.png").convert("RGBA")
    if poster.size != CANVAS_SIZE:
        poster = poster.resize(CANVAS_SIZE, Image.Resampling.LANCZOS)

    draw = ImageDraw.Draw(poster)
    number_font = load_font(73)

    for row in STANDINGS:
        pennant = Image.open(PENNANTS / f"{row['abbr']}.png").convert("RGBA")
        pennant = pennant.resize((PENNANT_W, PENNANT_H), Image.Resampling.LANCZOS)
        poster.alpha_composite(pennant, (PENNANT_X, int(row["y"] - PENNANT_H / 2)))

        draw_text_centered(draw, RECORD_X, row["y"], row["record"], number_font)
        draw_text_centered(draw, GB_X, row["y"], row["gb"], number_font)

    poster.save(OUTPUT)
    return OUTPUT

if __name__ == "__main__":
    out = render()
    print(f"Wrote {out}")
