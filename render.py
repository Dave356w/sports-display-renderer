from pathlib import Path
from datetime import datetime
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT     = Path(__file__).resolve().parent
ASSETS   = ROOT / "assets"
PENNANTS = ASSETS / "pennants"
OUT      = ROOT / "public" / "mlb_nl_west.png"

NL_WEST_ID = 203
TEAM_CODES  = {119: "LAD", 135: "SD", 109: "ARI", 115: "COL", 137: "SF"}
SLOT_Y      = [370, 600, 830, 1060, 1290]

NAVY      = (8, 42, 78)
RED       = (185, 28, 28)
FONT_BOLD = "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf"
FALLBACK  = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Date overlay — covers baked-in date in original background.png
DATE_CENTER_Y = 324
DATE_RECT     = [(160, 300), (800, 348)]
DATE_DASH_L   = [(163, 322), (205, 327)]
DATE_DASH_R   = [(755, 322), (797, 327)]


def fetch_standings():
    season = datetime.now().year
    url = (
        "https://statsapi.mlb.com/api/v1/standings"
        f"?leagueId=104&season={season}&standingsTypes=regularSeason"
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()

    for division in resp.json()["records"]:
        if division["division"]["id"] != NL_WEST_ID:
            continue
        teams = []
        for rank, record in enumerate(division["teamRecords"]):
            code = TEAM_CODES.get(record["team"]["id"])
            if not code:
                continue
            gb = record.get("gamesBack", "-")
            teams.append({
                "code": code,
                "wl":   f"{record['wins']}-{record['losses']}",
                "gb":   "—" if gb == "-" else gb,
                "y":    SLOT_Y[rank],
            })
        return teams

    raise ValueError("NL West division not found in API response")


def draw_date(img, draw):
    bg_color = img.getpixel((480, 260))[:3]   # sample parchment colour
    draw.rectangle(DATE_RECT, fill=bg_color)
    date_str = datetime.now().strftime("%B %-d, %Y").upper()
    draw.text((480, DATE_CENTER_Y), date_str, font=load_font(30), fill=NAVY, anchor="mm")
    draw.rectangle(DATE_DASH_L, fill=RED)
    draw.rectangle(DATE_DASH_R, fill=RED)


def load_font(size):
    path = FONT_BOLD if Path(FONT_BOLD).exists() else FALLBACK
    return ImageFont.truetype(path, size)


def main():
    teams = fetch_standings()
    print(f"Fetched standings for {datetime.now().strftime('%Y-%m-%d')}:")
    for t in teams:
        print(f"  {t['code']:3s}  {t['wl']:6s}  GB: {t['gb']}")

    bg_path = ASSETS / "background.png"
    if not bg_path.exists():
        raise FileNotFoundError(f"Missing {bg_path}")

    img  = Image.open(bg_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    font = load_font(48)

    draw_date(img, draw)

    pennant_x     = 50
    stat_y_offset = 85   # vertically centred with 170px pennant height
    wl_x, gb_x   = 755, 875

    for team in teams:
        pennant_path = PENNANTS / f"{team['code']}.png"
        if not pennant_path.exists():
            raise FileNotFoundError(f"Missing pennant: {pennant_path}")

        pennant = Image.open(pennant_path).convert("RGBA")
        img.alpha_composite(pennant, (pennant_x, team["y"]))

        y = team["y"] + stat_y_offset
        draw.text((wl_x, y), team["wl"], font=font, fill=NAVY, anchor="mm")
        draw.text((gb_x, y), team["gb"], font=font, fill=NAVY, anchor="mm")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT, quality=95)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
