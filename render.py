
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
PUBLIC_DIR.mkdir(exist_ok=True)

W, H = 480, 800
SEASON = int(os.getenv("MLB_SEASON", datetime.now().year))

TEAM_MAP = {
    "Los Angeles Dodgers": "LAD",
    "San Diego Padres": "SD",
    "Arizona Diamondbacks": "ARI",
    "Colorado Rockies": "COL",
    "San Francisco Giants": "SF",
}

COLORS = {
    "LAD": ((0, 90, 156), "DODGERS", "LA", (255, 255, 255)),
    "SD": ((47, 36, 29), "PADRES", "SD", (255, 196, 37)),
    "ARI": ((128, 0, 32), "D-BACKS", "A", (64, 224, 208)),
    "COL": ((51, 38, 94), "ROCKIES", "CR", (230, 230, 230)),
    "SF": ((20, 20, 20), "GIANTS", "SF", (253, 90, 30)),
}

ROW_Y = [190, 302, 414, 526, 638]


def font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def fetch_standings() -> List[Dict]:
    url = f"https://statsapi.mlb.com/api/v1/standings?leagueId=104&season={SEASON}&standingsTypes=regularSeason"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    teams = []

    for group in data.get("records", []):
        for rec in group.get("teamRecords", []):
            name = rec["team"]["name"]
            if name not in TEAM_MAP:
                continue
            gb = rec.get("gamesBack", "0.0")
            if gb in ("0.0", "0", "-", None):
                gb = "—"
            teams.append({
                "abbr": TEAM_MAP[name],
                "wins": int(rec["wins"]),
                "losses": int(rec["losses"]),
                "gb": str(gb),
                "rank": int(rec.get("divisionRank", 99)),
            })

    if len(teams) != 5:
        raise RuntimeError(f"Expected 5 NL West teams, got {len(teams)}")

    return sorted(teams, key=lambda x: x["rank"])


def sample_standings() -> List[Dict]:
    return [
        {"abbr": "LAD", "wins": 31, "losses": 16, "gb": "—", "rank": 1},
        {"abbr": "SD", "wins": 28, "losses": 19, "gb": "3.0", "rank": 2},
        {"abbr": "ARI", "wins": 24, "losses": 23, "gb": "7.0", "rank": 3},
        {"abbr": "COL", "wins": 16, "losses": 29, "gb": "14.0", "rank": 4},
        {"abbr": "SF", "wins": 15, "losses": 30, "gb": "15.0", "rank": 5},
    ]


def draw_pennant(draw: ImageDraw.ImageDraw, x: int, y: int, abbr: str):
    primary, name, mark, accent = COLORS[abbr]
    primary = tuple(primary)
    accent = tuple(accent)

    draw.polygon([(x+10,y+12),(x+255,y+26),(x+282,y+49),(x+255,y+72),(x+10,y+84)], fill=(0,0,0))
    draw.polygon([(x+8,y+8),(x+244,y+20),(x+276,y+48),(x+244,y+76),(x+8,y+88)], fill=primary)
    draw.line([(x+8,y+8),(x+244,y+20),(x+276,y+48),(x+244,y+76),(x+8,y+88),(x+8,y+8)], fill=(0,0,0), width=2)
    draw.polygon([(x+8,y+8),(x+28,y+9),(x+28,y+87),(x+8,y+88)], fill=accent)
    draw.text((x+54,y+49), mark, font=font(38, True), fill=accent, anchor="mm", stroke_width=1, stroke_fill=(0,0,0))
    draw.text((x+143,y+49), name, font=font(29, True), fill=accent, anchor="mm", stroke_width=1, stroke_fill=(0,0,0))


def render(standings: List[Dict], output: Path):
    cream = (238, 230, 211)
    navy = (8, 28, 52)
    red = (135, 24, 31)

    img = Image.new("RGB", (W, H), cream)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W-1, H-1], outline=(28,28,28), width=8)
    draw.rectangle([14, 14, W-15, H-15], outline=(45,45,45), width=5)
    draw.rectangle([24, 24, W-25, H-25], outline=(120,112,100), width=1)

    draw.text((W//2, 104), "NL WEST STANDINGS", font=font(45, True), fill=navy, anchor="mm")
    draw.line([96,143,143,143], fill=red, width=3)
    draw.line([337,143,384,143], fill=red, width=3)
    draw.text((W//2, 143), datetime.now().strftime("%b %d, %Y").upper(), font=font(23, True), fill=navy, anchor="mm")

    draw.text((355,176), "W-L", font=font(20, True), fill=navy, anchor="mm")
    draw.text((430,176), "GB", font=font(20, True), fill=navy, anchor="mm")

    for y in [289,401,513,625]:
        draw.line([135,y,445,y], fill=(190,180,165), width=1)

    score_font = font(26, True)

    for i, team in enumerate(standings):
        y = ROW_Y[i]
        draw_pennant(draw, 42, y-5, team["abbr"])
        draw.text((355, y+43), f"{team['wins']}-{team['losses']}", font=score_font, fill=navy, anchor="mm")
        draw.text((430, y+43), team["gb"], font=score_font, fill=navy, anchor="mm")

    draw.text((W//2,705), "★  GB = GAMES BACK  ★", font=font(14, True), fill=navy, anchor="mm")
    draw.text((W//2,752), "STANDINGS VIA MLB STATS API", font=font(11, True), fill=navy, anchor="mm")

    img.save(output)


def main():
    try:
        standings = fetch_standings()
        print("Fetched live standings")
    except Exception as exc:
        print(f"Using sample standings: {exc}")
        standings = sample_standings()

    render(standings, PUBLIC_DIR / "mlb_nl_west.png")
    print("Rendered public/mlb_nl_west.png")


if __name__ == "__main__":
    main()
