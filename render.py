"""
Render the 7.3-inch e-paper NL West standings collectible.

Composites the hand-curated parchment background (971x1619) with felt-style
team pennants (2172x724 source, scaled to fit each row), overlays the current
date between the title's red dashes, and writes W-L / GB stats into the
standings columns.
"""
from pathlib import Path
from datetime import datetime
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT     = Path(__file__).resolve().parent
ASSETS   = ROOT / "assets"
PENNANTS = ASSETS / "pennants"
OUT      = ROOT / "public" / "mlb_nl_west.png"

NL_WEST_ID  = 203
TEAM_CODES  = {119: "LAD", 135: "SD", 109: "ARI", 115: "COL", 137: "SF"}

# -- Layout constants (calibrated for the 971x1619 parchment background) -----
# Row dividers in the background sit at y = 627, 795, 963, 1132 -- 168 px apart.
# Five row slots run 459..627, 627..795, 795..963, 963..1131, 1131..1299.
SLOT_CENTER_Y = [543, 711, 879, 1047, 1215]

PENNANT_SCALE = 0.205                     # 2172x724 source -> ~445x148 (fits 168-px slot)
PENNANT_X     = 38                        # left margin inside the parchment frame

WL_X = 727                                # under the "W-L" header
GB_X = 875                                # under the "GB" header

# Date sits between the two red dashes baked into the background (y~340)
DATE_CENTER_Y = 340
DATE_CENTER_X = 487                       # midpoint of the gap between the dashes

NAVY      = (8, 42, 78)
FONT_BOLD = "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf"
FALLBACK  = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


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
                "code":   code,
                "wl":     f"{record['wins']}-{record['losses']}",
                "gb":     "—" if gb == "-" else gb,
                "center": SLOT_CENTER_Y[rank],
            })
        return teams

    raise ValueError("NL West division not found in API response")


def load_font(size):
    path = FONT_BOLD if Path(FONT_BOLD).exists() else FALLBACK
    return ImageFont.truetype(path, size)


def draw_date(draw):
    """Stamp today's date in the gap between the title dashes."""
    date_str = datetime.now().strftime("%B %-d, %Y").upper()
    draw.text(
        (DATE_CENTER_X, DATE_CENTER_Y),
        date_str,
        font=load_font(28),
        fill=NAVY,
        anchor="mm",
    )


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
    stat_font = load_font(48)

    draw_date(draw)

    for team in teams:
        pennant_path = PENNANTS / f"{team['code']}.png"
        if not pennant_path.exists():
            raise FileNotFoundError(f"Missing pennant: {pennant_path}")

        pennant = Image.open(pennant_path).convert("RGBA")
        pw = int(pennant.width  * PENNANT_SCALE)
        ph = int(pennant.height * PENNANT_SCALE)
        pennant = pennant.resize((pw, ph), Image.LANCZOS)
        # vertically center the pennant on the row's centerline
        py = team["center"] - ph // 2
        img.alpha_composite(pennant, (PENNANT_X, py))

        draw.text((WL_X, team["center"]), team["wl"], font=stat_font, fill=NAVY, anchor="mm")
        draw.text((GB_X, team["center"]), team["gb"], font=stat_font, fill=NAVY, anchor="mm")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT, quality=95)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
