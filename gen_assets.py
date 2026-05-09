"""
Downloads NL West team logos from mlbstatic.com and generates pennant PNGs.
Run once via the 'Regenerate Background Asset' GitHub Actions workflow.
render.py consumes the output PNGs directly — no runtime network calls needed.
"""
import io
import requests
import cairosvg
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ASSETS   = Path(__file__).parent / "assets"
PENNANTS = ASSETS / "pennants"

FONT_HEAVY = "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf"
FALLBACK   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Team IDs match statsapi.mlb.com — primary colours from official brand guidelines
NL_WEST = {
    119: {"code": "LAD", "name": "DODGERS",           "primary": (0,   90, 156), "text": (255, 255, 255)},
    135: {"code": "SD",  "name": "PADRES",            "primary": (47,  36,  29), "text": (255, 196,  37)},
    109: {"code": "ARI", "name": "D-BACKS",           "primary": (167, 25,  48), "text": (227, 181, 101)},
    115: {"code": "COL", "name": "COLORADO\nROCKIES", "primary": (51,  51, 102), "text": (255, 255, 255)},
    137: {"code": "SF",  "name": "GIANTS",            "primary": (39,  37,  31), "text": (253,  90,  30)},
}

PW, PH    = 650, 170   # must match render.py slot dimensions
LOGO_SIZE = 130
SILVER    = (160, 160, 155)


def fnt(size):
    p = Path(FONT_HEAVY)
    return ImageFont.truetype(str(p) if p.exists() else FALLBACK, size)


def fetch_logo(team_id):
    url = f"https://www.mlbstatic.com/team-logos/{team_id}.svg"
    svg = requests.get(url, timeout=15).content
    png = cairosvg.svg2png(bytestring=svg, output_width=LOGO_SIZE, output_height=LOGO_SIZE)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def make_pennant(team_id, info):
    img  = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Pennant triangle pointing right
    draw.polygon([(0, 0), (PW, PH // 2), (0, PH)], fill=info["primary"])

    # Grommets on left edge
    for gy in [PH // 4, PH // 2, 3 * PH // 4]:
        draw.ellipse([(2, gy - 8), (18, gy + 8)], fill=SILVER, outline=(110, 110, 105), width=1)

    # White circle backing so logo always contrasts against any pennant colour
    CIRCLE_R = LOGO_SIZE // 2 + 6
    cx = 22 + LOGO_SIZE // 2
    cy = PH // 2
    draw.ellipse([(cx - CIRCLE_R, cy - CIRCLE_R), (cx + CIRCLE_R, cy + CIRCLE_R)],
                 fill=(255, 255, 255))

    # Team logo — centred over the white circle
    logo    = fetch_logo(team_id)
    paste_y = (PH - LOGO_SIZE) // 2
    img.alpha_composite(logo, (22, paste_y))

    # Team name — centred in right portion
    text_cx = (22 + LOGO_SIZE + PW) // 2   # ≈ 383
    name    = info["name"]
    color   = info["text"]

    if "\n" in name:
        top, bot = name.split("\n")
        draw.text((text_cx, PH // 2 - 20), top, font=fnt(30), fill=color, anchor="mm")
        draw.text((text_cx, PH // 2 + 20), bot, font=fnt(30), fill=color, anchor="mm")
    else:
        draw.text((text_cx, PH // 2), name, font=fnt(36), fill=color, anchor="mm")

    return img


def main():
    PENNANTS.mkdir(parents=True, exist_ok=True)
    for team_id, info in NL_WEST.items():
        print(f"Fetching logo for {info['code']} (team {team_id})...")
        pennant = make_pennant(team_id, info)
        out = PENNANTS / f"{info['code']}.png"
        pennant.save(out, "PNG")
        print(f"  Wrote {out}")


if __name__ == "__main__":
    main()
