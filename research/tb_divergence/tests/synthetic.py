"""Deterministic fake seasons, so the leakage tests run without network."""
from __future__ import annotations

import numpy as np
import pandas as pd

TEAM_IDS = list(range(101, 131))


def make_season(
    n_days: int = 200,
    games_per_day: int = 12,
    seed: int = 7,
    start: str = "2025-04-01",
) -> pd.DataFrame:
    """A schedule with two start times per day, so same-day ordering matters."""
    rng = np.random.default_rng(seed)
    base = pd.Timestamp(start, tz="UTC")

    rows = []
    game_pk = 700000
    for day in range(n_days):
        teams = rng.permutation(TEAM_IDS)
        for slot in range(games_per_day):
            home_id = int(teams[(2 * slot) % len(teams)])
            away_id = int(teams[(2 * slot + 1) % len(teams)])
            if home_id == away_id:
                continue
            # Afternoon or evening first pitch.
            hour = 17 if slot % 2 == 0 else 23
            start_utc = base + pd.Timedelta(days=day, hours=hour)
            home_tb = float(rng.poisson(14))
            away_tb = float(rng.poisson(13))
            rows.append(
                {
                    "game_pk": game_pk,
                    "start_utc": start_utc,
                    "home_id": home_id,
                    "away_id": away_id,
                    "home_tb": home_tb,
                    "away_tb": away_tb,
                    "home_pa": float(rng.integers(34, 42)),
                    "away_pa": float(rng.integers(34, 42)),
                    "home_won": int(home_tb + rng.normal(0, 3) > away_tb),
                }
            )
            game_pk += 1
    return pd.DataFrame(rows)


def poison_from(games: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Replace every outcome at or after `cutoff` with absurd values.

    If a feature computed before `cutoff` changes, the pipeline read the
    future.
    """
    poisoned = games.copy()
    mask = poisoned["start_utc"] >= cutoff
    poisoned.loc[mask, "home_tb"] = 999.0
    poisoned.loc[mask, "away_tb"] = 1.0
    poisoned.loc[mask, "home_pa"] = 99.0
    poisoned.loc[mask, "away_pa"] = 99.0
    poisoned.loc[mask, "home_won"] = 1
    return poisoned
