"""
MLB StatsAPI ingestion for the divergence signal, with an on-disk cache.

Boxscores are immutable once a game is final, so every fetched game is cached
as a JSON line and never re-requested. That keeps repeated backtests off the
API and makes runs reproducible.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def total_bases(batting: dict) -> float | None:
    """Total bases from a boxscore batting block.

    StatsAPI reports totalBases directly; the fallback recomputes it. `hits`
    already counts doubles, triples and homers once, so each extra-base hit
    needs only its additional bases.
    """
    if batting.get("totalBases") is not None:
        return float(batting["totalBases"])
    if "hits" not in batting:
        return None
    return float(
        batting.get("hits", 0)
        + batting.get("doubles", 0)
        + 2 * batting.get("triples", 0)
        + 3 * batting.get("homeRuns", 0)
    )


def plate_appearances(batting: dict) -> float | None:
    for key in ("plateAppearances", "atBats"):
        if batting.get(key):
            return float(batting[key])
    return None


def fetch_schedule(start_date: str, end_date: str, session: requests.Session | None = None) -> pd.DataFrame:
    """Completed regular-season games between two ISO dates, inclusive.

    Uses `gameDate` (a UTC first-pitch timestamp) rather than the slate date,
    so games on the same calendar day stay correctly ordered -- including both
    halves of a doubleheader.
    """
    session = session or requests.Session()
    url = (
        f"{BASE_URL}/schedule?sportId=1&startDate={start_date}"
        f"&endDate={end_date}&gameType=R"
    )
    payload = session.get(url, timeout=30).json()

    rows = []
    for date_block in payload.get("dates", []):
        for game in date_block.get("games", []):
            if game.get("status", {}).get("abstractGameState") != "Final":
                continue
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            home_score, away_score = home.get("score"), away.get("score")
            if home_score is None or away_score is None or home_score == away_score:
                # No decided result to grade against (tie, or score not posted).
                continue
            rows.append(
                {
                    "game_pk": game["gamePk"],
                    "start_utc": pd.to_datetime(game["gameDate"], utc=True),
                    "home_id": home["team"]["id"],
                    "home_team": home["team"]["name"],
                    "away_id": away["team"]["id"],
                    "away_team": away["team"]["name"],
                    "home_won": int(home_score > away_score),
                }
            )

    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .drop_duplicates("game_pk")
        .sort_values("start_utc", kind="mergesort")
        .reset_index(drop=True)
    )


def _cache_path(game_pk: int) -> Path:
    return CACHE_DIR / f"{game_pk}.json"


def _fetch_box(game_pk: int, session: requests.Session, retries: int = 3) -> dict | None:
    cached = _cache_path(game_pk)
    if cached.exists():
        return json.loads(cached.read_text())

    url = f"{BASE_URL}/game/{game_pk}/boxscore"
    for attempt in range(retries):
        try:
            payload = session.get(url, timeout=30).json()
            teams = payload.get("teams", {})
            home_bat = teams.get("home", {}).get("teamStats", {}).get("batting", {})
            away_bat = teams.get("away", {}).get("teamStats", {}).get("batting", {})
            record = {
                "game_pk": game_pk,
                "home_tb": total_bases(home_bat),
                "away_tb": total_bases(away_bat),
                "home_pa": plate_appearances(home_bat),
                "away_pa": plate_appearances(away_bat),
            }
            if record["home_tb"] is None or record["away_tb"] is None:
                return None
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(record))
            return record
        except (requests.RequestException, ValueError):
            if attempt == retries - 1:
                return None
            time.sleep(2**attempt)
    return None


def hydrate(schedule: pd.DataFrame, max_workers: int = 8) -> pd.DataFrame:
    """Attach boxscore totals to a schedule frame.

    Games whose boxscore cannot be read are dropped and counted -- a silent
    drop would quietly thin the league averages the signal divides by.
    """
    if schedule.empty:
        return schedule

    session = requests.Session()
    records = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_box, int(pk), session): int(pk)
            for pk in schedule["game_pk"]
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                records.append(result)

    dropped = len(schedule) - len(records)
    if dropped:
        print(f"warning: dropped {dropped}/{len(schedule)} games with unreadable boxscores")

    box = pd.DataFrame(records)
    merged = schedule.merge(box, on="game_pk", how="inner")
    return merged.sort_values("start_utc", kind="mergesort").reset_index(drop=True)


def load_games(start_date: str, end_date: str, max_workers: int = 8) -> pd.DataFrame:
    """Schedule + boxscores for a date range, ready for `signal.build_features`."""
    schedule = fetch_schedule(start_date, end_date)
    if schedule.empty:
        raise ValueError(f"no completed regular-season games between {start_date} and {end_date}")
    return hydrate(schedule, max_workers=max_workers)
