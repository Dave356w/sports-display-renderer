from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
PENNANTS = ASSETS / "pennants"
OUT = ROOT / "public" / "mlb_nl_west.png"

TEAMS = [
    {"code": "LAD", "wl": "31-16", "gb": "—",   "y": 370},
    {"code": "SD",  "wl": "28-19", "gb": "3.0", "y": 600},
    {"code": "ARI", "wl": "24-23", "gb": "7.0", "y": 830},
    {"code": "COL", "wl": "16-29", "gb": "14.0","y": 1060},
    {"code": "SF",  "wl": "15-30", "gb": "15.0","y": 1290},
]

NAVY = (8, 42, 78)
FONT_BOLD = "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf"
FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def load_font(size):
    path = FONT_BOLD if Path(FONT_BOLD).exists() else FALLBACK
    return ImageFont.truetype(path, size)

def main():
    bg_path = ASSETS / "background.png"
    if not bg_path.exists():
        raise FileNotFoundError(f"Missing {bg_path}")

    img = Image.open(bg_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    record_font = load_font(48)

    pennant_x = 60
    stat_y_offset = 62
    wl_x = 730
    gb_x = 865

    for team in TEAMS:
        pennant_path = PENNANTS / f"{team['code']}.png"
        if not pennant_path.exists():
            raise FileNotFoundError(f"Missing {pennant_path}")

        pennant = Image.open(pennant_path).convert("RGBA")
        img.alpha_composite(pennant, (pennant_x, team["y"]))

        y = team["y"] + stat_y_offset
        draw.text((wl_x, y), team["wl"], font=record_font, fill=NAVY, anchor="mm")
        draw.text((gb_x, y), team["gb"], font=record_font, fill=NAVY, anchor="mm")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT, quality=95)
    print(f"Wrote {OUT}")

if __name__ == "__main__":
    main()
