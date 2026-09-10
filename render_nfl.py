"""Render the 7.3-inch e-paper NFC West standings collectible.

This is the NFL companion to ``render.py``. It follows the same device path:
compose at the 971x1619 artwork resolution, overlay live data, then downsample
to the reTerminal E1002's native 480x800 portrait pixel grid.

Static artwork lives in ``assets/nfl/``. Dynamic overlays are:
  * current Pacific date
  * NFC West W-L, division record, and games behind
  * the current NFL week's NFC West matchups, showing the Pacific kickoff time
    until a game is final and its score afterwards

Schedules and results come from nflverse's ``games.csv``, a keyless static file
served off GitHub. Standings are computed from completed regular-season games
rather than read from a standings feed. Set ``NFL_SAMPLE=1`` for a
deterministic 2026 Week 1 layout test.
"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timedelta
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

# nflverse publishes every scheduled and completed game in one CSV. It is a
# plain file on GitHub, not an API, so there is no key, quota, or user-agent
# gate to trip over on a CI runner.
GAMES_CSV = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
# The file's `gametime` column is a bare HH:MM in US Eastern.
SOURCE_TZ = ZoneInfo("America/New_York")

NFC_WEST = ("SF", "SEA", "LAR", "ARI")
TIE_ORDER = {abbr: i for i, abbr in enumerate(NFC_WEST)}
# nflverse abbreviates the Rams "LA"; the artwork and layout use "LAR".
TEAM_ALIASES = {"LA": "LAR"}

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

# A game still counts as "this week" for a while after kickoff so the display
# does not roll forward mid-afternoon while games are being played.
IN_PROGRESS_GRACE = timedelta(hours=6)


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


def team(abbr: str) -> str:
    return TEAM_ALIASES.get(abbr, abbr)


def season_for(now: datetime) -> int:
    """The NFL season a date belongs to; January and February are last year's."""
    return now.year - 1 if now.month < 3 else now.year


def _kickoff(gameday: str, gametime: str) -> datetime | None:
    try:
        naive = datetime.strptime(f"{gameday} {gametime}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=SOURCE_TZ).astimezone(DISPLAY_TZ)


def _score(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_games(season: int) -> list[dict]:
    """Every game in `season`, normalised to the abbreviations the layout uses.

    Falls back to the newest season in the file when `season` is not published
    yet — the schedule usually lands in spring, months after the season flips.
    """
    response = http_session().get(GAMES_CSV, timeout=30)
    response.raise_for_status()

    by_season: dict[int, list[dict]] = {}
    for row in csv.DictReader(io.StringIO(response.text)):
        try:
            row_season = int(row["season"])
            week = int(row["week"])
        except (KeyError, TypeError, ValueError):
            continue
        kickoff = _kickoff(row.get("gameday", ""), row.get("gametime", ""))
        if kickoff is None:
            continue
        home, away = _score(row.get("home_score", "")), _score(row.get("away_score", ""))
        by_season.setdefault(row_season, []).append({
            "week": week,
            "game_type": row.get("game_type", ""),
            "dt": kickoff,
            "home": team(row.get("home_team", "")),
            "away": team(row.get("away_team", "")),
            "home_score": home,
            "away_score": away,
            "played": home is not None and away is not None,
            "div_game": row.get("div_game", "") == "1",
        })

    if not by_season:
        return []
    games = by_season.get(season)
    if games is None:
        games = by_season[max(by_season)]
    return sorted(games, key=lambda g: g["dt"])


def compute_standings(games: list[dict]) -> list[dict]:
    """Build NFC West records from completed regular-season games."""
    tally = {
        abbr: {"abbr": abbr, "wins": 0, "losses": 0, "ties": 0,
               "div_wins": 0, "div_losses": 0, "div_ties": 0}
        for abbr in NFC_WEST
    }

    for game in games:
        if game["game_type"] != "REG" or not game["played"]:
            continue
        sides = (
            (game["home"], game["home_score"], game["away_score"]),
            (game["away"], game["away_score"], game["home_score"]),
        )
        for abbr, scored, allowed in sides:
            row = tally.get(abbr)
            if row is None:
                continue
            if scored > allowed:
                outcome = "wins"
            elif scored < allowed:
                outcome = "losses"
            else:
                outcome = "ties"
            row[outcome] += 1
            if game["div_game"]:
                row[f"div_{outcome}"] += 1

    return _finalize_standings(list(tally.values()))


def _record(wins: int, losses: int, ties: int) -> str:
    return f"{wins}-{losses}" + (f"-{ties}" if ties else "")


def _finalize_standings(rows: list[dict]) -> list[dict]:
    by_team = {r["abbr"]: r for r in rows}
    for abbr in NFC_WEST:
        by_team.setdefault(abbr, {"abbr": abbr, "wins": 0, "losses": 0, "ties": 0,
                                  "div_wins": 0, "div_losses": 0, "div_ties": 0})

    def pct(r):
        games = r["wins"] + r["losses"] + r["ties"]
        return (r["wins"] + 0.5 * r["ties"]) / games if games else 0.0

    ordered = sorted(
        by_team.values(),
        key=lambda r: (-pct(r), -r["wins"], r["losses"], TIE_ORDER[r["abbr"]]),
    )
    leader = ordered[0]
    for row in ordered:
        row["wl"] = _record(row["wins"], row["losses"], row["ties"])
        row["div"] = _record(row.get("div_wins", 0), row.get("div_losses", 0), row.get("div_ties", 0))
        gb = ((leader["wins"] - row["wins"]) + (row["losses"] - leader["losses"])) / 2.0
        row["gb"] = "—" if gb <= 0 else (str(int(gb)) if gb.is_integer() else f"{gb:.1f}")
    return ordered


def current_week(games: list[dict], now: datetime) -> int | None:
    """The week to headline: the next one with a game left to play.

    Judged across the whole league, not just the NFC West, so a week in which
    all four clubs are on bye still reports its own number.
    """
    remaining = [g["week"] for g in games if g["dt"] >= now - IN_PROGRESS_GRACE]
    if remaining:
        return min(remaining)
    return max((g["week"] for g in games), default=None)


def week_schedule(games: list[dict], week: int | None) -> list[dict]:
    if week is None:
        return []
    division = set(NFC_WEST)
    return [
        g for g in games
        if g["week"] == week and ({g["home"], g["away"]} & division)
    ]


def sample_season() -> tuple[list[dict], int]:
    """Deterministic 2026 Week 1 stand-in for layout tests.

    The opener is final and the rest are upcoming, so one pass exercises both
    the score line and the kickoff-time line.
    """
    games = [
        {"week": 1, "game_type": "REG", "away": "NE", "home": "SEA",
         "dt": datetime(2026, 9, 9, 17, 20, tzinfo=DISPLAY_TZ),
         "home_score": 24, "away_score": 17, "played": True},
        {"week": 1, "game_type": "REG", "away": "SF", "home": "LAR",
         "dt": datetime(2026, 9, 10, 17, 35, tzinfo=DISPLAY_TZ)},
        {"week": 1, "game_type": "REG", "away": "ARI", "home": "LAC",
         "dt": datetime(2026, 9, 13, 13, 25, tzinfo=DISPLAY_TZ)},
    ]
    for game in games:
        game.setdefault("home_score", None)
        game.setdefault("away_score", None)
        game.setdefault("played", False)
        game["div_game"] = False
    return games, 1


def _ordered_sides(game: dict) -> tuple[tuple[str, int | None], tuple[str, int | None], str]:
    """The two clubs in the order the matchup line prints them, plus the separator.

    A division club hosting an outside opponent leads ("SEA vs NE"); every other
    pairing, including division-on-division, reads away-first ("SF @ LAR").
    Scores are carried alongside so the score line stays in the same left-to-right
    order as the names above it.
    """
    away, home = game["away"], game["home"]
    if home in NFC_WEST and away not in NFC_WEST:
        return (home, game.get("home_score")), (away, game.get("away_score")), "vs"
    return (away, game.get("away_score")), (home, game.get("home_score")), "@"


def matchup_label(game: dict) -> str:
    (first, _), (second, _), separator = _ordered_sides(game)
    return f"{first} {separator} {second}"


def score_label(game: dict) -> str:
    (_, first), (_, second), _ = _ordered_sides(game)
    return f"FINAL {first}-{second}"


def time_label(dt: datetime) -> str:
    return dt.strftime("%a %-I:%M %p").upper()


def status_label(game: dict) -> str:
    """Final score once the game is in the books, otherwise the kickoff time."""
    if game.get("played"):
        return score_label(game)
    return time_label(game["dt"])


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
            draw.text((cx, 1492), status_label(game), font=load_font(FONT_BOLD, time_size), fill=NAVY, anchor="mm")
    else:
        draw.text((486, 1464), "SCHEDULE UNAVAILABLE", font=load_font(FONT_BOLD, 28), fill=NAVY, anchor="mm")

    draw.rectangle((68, 1547, 160, 1584), fill=(255, 255, 255, 255))
    draw.text((83, 1565), f"WEEK {week or '—'}", font=load_font(FONT_BOLD, 17), fill=NAVY, anchor="lm")

    img = img.resize(DEVICE_OUTPUT_SIZE, Image.Resampling.LANCZOS)
    return img.convert("RGB")


def main():
    now = datetime.now(DISPLAY_TZ)

    if os.getenv("NFL_SAMPLE") == "1":
        games, week = sample_season()
    else:
        try:
            games = fetch_games(season_for(now))
            week = current_week(games, now)
        except Exception as exc:
            print(f"WARNING: nflverse fetch failed: {exc}")
            games, week = [], None

    standings = compute_standings(games)
    schedule = week_schedule(games, week)

    print(f"NFC West render {now:%Y-%m-%d %H:%M %Z} — week {week or '—'}")
    for row in standings:
        print(f"  {row['abbr']:3s} {row['wl']:6s} DIV {row['div']:6s} GB {row['gb']}")
    for game in schedule:
        when = f"{game['dt']:%a %Y-%m-%d %-I:%M %p %Z}"
        print(f"  {matchup_label(game):12s} {status_label(game):16s} {when}")

    output = render(standings, week, schedule, now)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    output.save(OUT, format="PNG", optimize=True)
    print(f"Wrote {OUT} ({output.size[0]}x{output.size[1]})")


if __name__ == "__main__":
    main()
