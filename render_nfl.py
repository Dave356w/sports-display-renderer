"""Render the 7.3-inch e-paper NFC West standings collectible.

This is the NFL companion to ``render.py``. It follows the same device path:
compose at the 971x1619 artwork resolution, overlay live data, then downsample
to the reTerminal E1002's native 480x800 portrait pixel grid.

Static artwork lives in ``assets/nfl/``. Dynamic overlays are:
  * current Pacific date
  * NFC West W-L, division record, and games behind
  * the current NFL week's NFC West matchups and Pacific kickoff times

Data is keyless and fetched from ESPN's public NFL JSON feeds. Set
``NFL_SAMPLE=1`` for a deterministic 2026 Week 1 layout test.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets" / "nfl"
OUT = ROOT / "public" / "nfl_nfc_west.png"
DISPLAY_TZ = ZoneInfo("America/Los_Angeles")
DEVICE_OUTPUT_SIZE = (480, 800)

ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

NFC_WEST = ("SF", "SEA", "LAR", "ARI")
TIE_ORDER = {abbr: i for i, abbr in enumerate(NFC_WEST)}

NAVY = (4, 43, 78)
GREY = (118, 135, 148)
FONT_HEAVY = "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"
FONT_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-Bold.ttf"
FONT_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Calibrated against the 971x1619 white-background NFC West master.
ROW_CENTERS = (458, 704, 950, 1196)
STAT_X = (610, 744, 865)  # W-L, DIV, GB
DATE_X, DATE_Y = 486, 273
PENNANT_LEFT = 48
PENNANT_MAX_SIZE = (487, 182)


def load_font(path: str, size: int):
    return ImageFont.truetype(path if Path(path).exists() else FONT_FALLBACK, size)


def http_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "sports-display-renderer/1.0"
    return s


def _stat(entry: dict, aliases: tuple[str, ...], default=None):
    wanted = {x.lower() for x in aliases}
    for stat in entry.get("stats", []):
        names = {
            str(stat.get("name", "")).lower(),
            str(stat.get("abbreviation", "")).lower(),
            str(stat.get("shortDisplayName", "")).lower(),
        }
        if names & wanted:
            # displayValue is preferable for records such as "2-1".
            if stat.get("displayValue") is not None and any(
                x in wanted for x in ("divisionrecord", "div", "division")
            ):
                return stat["displayValue"]
            if stat.get("value") is not None:
                return stat["value"]
            return stat.get("displayValue", default)
    return default


def _standings_entries(node):
    """Yield ESPN standings entries without relying on one group nesting shape."""
    if isinstance(node, dict):
        standings = node.get("standings")
        if isinstance(standings, dict) and isinstance(standings.get("entries"), list):
            yield from standings["entries"]
        for value in node.values():
            yield from _standings_entries(value)
    elif isinstance(node, list):
        for value in node:
            yield from _standings_entries(value)


def _finalize_standings(rows: list[dict]) -> list[dict]:
    by_team = {r["abbr"]: r for r in rows}
    for abbr in NFC_WEST:
        by_team.setdefault(abbr, {"abbr": abbr, "wins": 0, "losses": 0, "ties": 0, "div": "0-0"})

    def pct(r):
        games = r["wins"] + r["losses"] + r["ties"]
        return (r["wins"] + 0.5 * r["ties"]) / games if games else 0.0

    ordered = sorted(
        by_team.values(),
        key=lambda r: (-pct(r), -r["wins"], r["losses"], TIE_ORDER[r["abbr"]]),
    )
    leader = ordered[0]
    for row in ordered:
        row["wl"] = f"{row['wins']}-{row['losses']}" + (f"-{row['ties']}" if row["ties"] else "")
        gb = ((leader["wins"] - row["wins"]) + (row["losses"] - leader["losses"])) / 2.0
        row["gb"] = "—" if gb <= 0 else (str(int(gb)) if gb.is_integer() else f"{gb:.1f}")
    return ordered


def fetch_standings(season: int) -> list[dict]:
    if os.getenv("NFL_SAMPLE") == "1":
        return _finalize_standings([
            {"abbr": a, "wins": 0, "losses": 0, "ties": 0, "div": "0-0"} for a in NFC_WEST
        ])

    response = http_session().get(
        ESPN_STANDINGS,
        params={"season": season, "seasontype": 2},
        timeout=20,
    )
    response.raise_for_status()

    found = []
    seen = set()
    for entry in _standings_entries(response.json()):
        abbr = (entry.get("team") or {}).get("abbreviation")
        if abbr not in NFC_WEST or abbr in seen:
            continue
        seen.add(abbr)
        found.append({
            "abbr": abbr,
            "wins": int(float(_stat(entry, ("wins", "w"), 0) or 0)),
            "losses": int(float(_stat(entry, ("losses", "l"), 0) or 0)),
            "ties": int(float(_stat(entry, ("ties", "t"), 0) or 0)),
            "div": str(_stat(entry, ("divisionrecord", "div", "division"), "0-0")),
        })
    return _finalize_standings(found)


def sample_schedule():
    return 1, [
        {"away": "NE", "home": "SEA", "dt": datetime(2026, 9, 9, 17, 20, tzinfo=DISPLAY_TZ)},
        {"away": "SF", "home": "LAR", "dt": datetime(2026, 9, 10, 17, 35, tzinfo=DISPLAY_TZ)},
        {"away": "ARI", "home": "LAC", "dt": datetime(2026, 9, 13, 13, 25, tzinfo=DISPLAY_TZ)},
    ]


def fetch_week_schedule() -> tuple[int | None, list[dict]]:
    if os.getenv("NFL_SAMPLE") == "1":
        return sample_schedule()

    response = http_session().get(ESPN_SCOREBOARD, params={"limit": 100}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    week = (payload.get("week") or {}).get("number")
    games = []

    for event in payload.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors") or []
        by_side = {c.get("homeAway"): c for c in competitors}
        home = ((by_side.get("home") or {}).get("team") or {}).get("abbreviation")
        away = ((by_side.get("away") or {}).get("team") or {}).get("abbreviation")
        if not home or not away or not ({home, away} & set(NFC_WEST)):
            continue
        try:
            kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(DISPLAY_TZ)
        except (KeyError, TypeError, ValueError):
            continue
        games.append({"away": away, "home": home, "dt": kickoff})

    # One SF-LAR event should appear once, not once for each NFC West club.
    unique = {(g["away"], g["home"], g["dt"].isoformat()): g for g in games}
    return week, sorted(unique.values(), key=lambda g: g["dt"])


def matchup_label(game: dict) -> str:
    away, home = game["away"], game["home"]
    if away in NFC_WEST and home in NFC_WEST:
        return f"{away} @ {home}"
    if home in NFC_WEST:
        return f"{home} vs {away}"
    return f"{away} @ {home}"


def time_label(dt: datetime) -> str:
    return dt.strftime("%a %-I:%M %p").upper()


def load_pennant(abbr: str) -> Image.Image:
    pennant_path = ASSETS / f"{abbr}.png"
    if not pennant_path.exists():
        raise FileNotFoundError(f"Missing NFL pennant artwork: {pennant_path}")
    pennant = Image.open(pennant_path).convert("RGBA")
    pennant.thumbnail(PENNANT_MAX_SIZE, Image.Resampling.LANCZOS)
    return pennant


def render(standings: list[dict], week: int | None, games: list[dict], now: datetime) -> Image.Image:
    background = ASSETS / "background.png"
    if not background.exists():
        raise FileNotFoundError(f"Missing NFL background artwork: {background}")
    img = Image.open(background).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # The generated background includes placeholder date/footer text, so clear
    # those dynamic zones and redraw them with live values.
    draw.rectangle((275, 230, 697, 303), fill=(255, 255, 255, 255))
    draw.text(
        (DATE_X, DATE_Y),
        now.strftime("%B %-d, %Y").upper(),
        font=load_font(FONT_SERIF, 39),
        fill=NAVY,
        anchor="mm",
    )

    stat_font = load_font(FONT_SERIF, 51)
    for slot, row in enumerate(standings[:4]):
        pennant = load_pennant(row['abbr'])
        cy = ROW_CENTERS[slot]
        py = int(cy - pennant.height / 2)
        img.alpha_composite(pennant, (PENNANT_LEFT, py))
        draw.text((STAT_X[0], cy), row["wl"], font=stat_font, fill=NAVY, anchor="mm")
        draw.text((STAT_X[1], cy), row["div"], font=stat_font, fill=NAVY, anchor="mm")
        draw.text((STAT_X[2], cy), row["gb"], font=stat_font, fill=NAVY, anchor="mm")

    # Variable-width weekly schedule columns: normally 3 or 4 unique games.
    if games:
        games = games[:4]
        left, right = 74, 897
        width = (right - left) / len(games)
        for i, game in enumerate(games):
            cx = left + width * (i + 0.5)
            if i:
                sx = int(left + width * i)
                draw.line((sx, 1413, sx, 1510), fill=GREY, width=2)
            name_size = 31 if len(games) <= 3 else 24
            time_size = 27 if len(games) <= 3 else 21
            draw.text((cx, 1442), matchup_label(game), font=load_font(FONT_HEAVY, name_size), fill=NAVY, anchor="mm")
            draw.text((cx, 1492), time_label(game["dt"]), font=load_font(FONT_BOLD, time_size), fill=NAVY, anchor="mm")
    else:
        draw.text((486, 1464), "SCHEDULE UNAVAILABLE", font=load_font(FONT_BOLD, 28), fill=NAVY, anchor="mm")

    draw.rectangle((68, 1547, 160, 1584), fill=(255, 255, 255, 255))
    draw.text((83, 1565), f"WEEK {week or '—'}", font=load_font(FONT_BOLD, 17), fill=NAVY, anchor="lm")

    img = img.resize(DEVICE_OUTPUT_SIZE, Image.Resampling.LANCZOS)
    return img.convert("RGB")


def main():
    now = datetime.now(DISPLAY_TZ)
    try:
        standings = fetch_standings(now.year)
    except Exception as exc:
        print(f"WARNING: standings fetch failed: {exc}")
        standings = _finalize_standings([
            {"abbr": a, "wins": 0, "losses": 0, "ties": 0, "div": "0-0"} for a in NFC_WEST
        ])

    try:
        week, games = fetch_week_schedule()
    except Exception as exc:
        print(f"WARNING: schedule fetch failed: {exc}")
        week, games = None, []

    print(f"NFC West render {now:%Y-%m-%d %H:%M %Z}")
    for row in standings:
        print(f"  {row['abbr']:3s} {row['wl']:6s} DIV {row['div']:6s} GB {row['gb']}")
    for game in games:
        print(f"  {matchup_label(game):12s} {game['dt']:%a %Y-%m-%d %-I:%M %p %Z}")

    output = render(standings, week, games, now)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    output.save(OUT, format="PNG", optimize=True)
    print(f"Wrote {OUT} ({output.size[0]}x{output.size[1]})")


if __name__ == "__main__":
    main()
