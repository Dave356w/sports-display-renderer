"""
Render the 7.3-inch e-paper NL West standings collectible.

Composites the hand-curated parchment background (971x1619) with felt-style
team pennants (2172x724 source, scaled to fit each row), overlays the current
date between the title's red dashes, and writes W-L / GB stats into the
standings columns. The composited canvas is then downsampled to the device's
native pixel grid and (optionally) rotated for vertical mounting.

Target device: reTerminal E1002 (E Ink Spectra 6, 7.3", 800x480 native).

Layout & rotation flags
-----------------------
DEVICE_OUTPUT_SIZE         portrait native pixel dims to downsample to before
                           rotation (set to None to keep the high-res master).
ROTATE_FOR_PORTRAIT_MOUNT  rotate the saved PNG so a landscape-native panel
                           reads upright when mounted with its long edge
                           vertical. ROTATE_ANGLE = -90 = cable on the right.
"""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT     = Path(__file__).resolve().parent
ASSETS   = ROOT / "assets"
PENNANTS = ASSETS / "pennants"
OUT      = ROOT / "public" / "mlb_nl_west.png"

NL_WEST_ID  = 203
TEAM_CODES  = {119: "LAD", 135: "SD", 109: "ARI", 115: "COL", 137: "SF"}
DISPLAY_TZ  = ZoneInfo("America/Los_Angeles")   # date shown on display

# -- Device output -----------------------------------------------------------
# reTerminal E1002 is 800x480 landscape native. We compose portrait at high
# res, then downsample to 480x800 portrait, then (optionally) rotate to
# 800x480 landscape for the device buffer. Set to None to skip downsampling.
DEVICE_OUTPUT_SIZE = (480, 800)            # (width, height) PORTRAIT

ROTATE_FOR_PORTRAIT_MOUNT = False
ROTATE_ANGLE              = -90            # use +90 if the cable ends up on the left

# -- Layout constants (calibrated for the 971x1619 parchment background) -----
# Row dividers in the background sit at y = 627, 795, 963, 1132 -- 168 px apart.
# Five row slots run 459..627, 627..795, 795..963, 963..1131, 1131..1299.
SLOT_CENTER_Y = [543, 711, 879, 1047, 1215]

PENNANT_SCALE = 0.205                      # 2172x724 source -> ~445x148 (fits 168-px slot)
PENNANT_X     = 38                         # left margin inside the parchment frame

WL_X = 727                                 # under the "W-L" header
GB_X = 875                                 # under the "GB" header

# Date sits between the two red dashes baked into the background (y~340)
DATE_CENTER_Y = 340
DATE_CENTER_X = 487                        # midpoint of the gap between the dashes

# Font sizes are tuned for the 971x1619 master canvas. After downsampling to
# 480x800 they render at ~half height. STAT_FONT_SIZE 48 -> ~24 px (fine);
# DATE_FONT_SIZE 36 -> ~18 px (legible between the dashes).
STAT_FONT_SIZE = 48
DATE_FONT_SIZE = 36

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
    date_str = datetime.now(DISPLAY_TZ).strftime("%B %-d, %Y").upper()
    draw.text(
        (DATE_CENTER_X, DATE_CENTER_Y),
        date_str,
        font=load_font(DATE_FONT_SIZE),
        fill=NAVY,
        anchor="mm",
    )


def main():
    teams = fetch_standings()
    print(f"Fetched standings for {datetime.now(DISPLAY_TZ).strftime('%Y-%m-%d')}:")
    for t in teams:
        print(f"  {t['code']:3s}  {t['wl']:6s}  GB: {t['gb']}")

    bg_path = ASSETS / "background_.png"
    if not bg_path.exists():
        raise FileNotFoundError(f"Missing {bg_path}")

    img  = Image.open(bg_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    stat_font = load_font(STAT_FONT_SIZE)

    draw_date(draw)

    for team in teams:
        pennant_path = ASSETS / f"{team['code']}_.png"
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

    if DEVICE_OUTPUT_SIZE:
        # Downsample to native panel resolution so the device paints pixels 1:1.
        img = img.resize(DEVICE_OUTPUT_SIZE, Image.LANCZOS)

    if ROTATE_FOR_PORTRAIT_MOUNT:
        # NEAREST keeps pixels exact (no resampling) on the 90° transpose.
        img = img.rotate(ROTATE_ANGLE, expand=True, resample=Image.NEAREST)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT, quality=95)
    print(f"Wrote {OUT}  ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
