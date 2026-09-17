"""
Sanity checks that separate a real edge from a construction artifact.

The one that matters most: `venue_sample_profile`. A club's short-window
rating is built only from games at the venue it is about to play, so a team
with no recent home games falls back to the league mean. That pulls
`delta_short` toward zero and therefore pushes `|div_delta|` *up*. If the
high-threshold buckets are simply the games with the least evidence behind
them, the threshold is selecting for missing data, not for divergence.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def venue_sample_profile(features: pd.DataFrame, thresholds=(0.0, 0.15, 0.30)) -> pd.DataFrame:
    """Venue-sample sizes and win rates by threshold bucket."""
    rows = []
    for threshold in thresholds:
        sub = features[features["abs_div_delta"] >= threshold]
        if sub.empty:
            continue
        rows.append(
            {
                "threshold": threshold,
                "n": len(sub),
                "mean_home_venue_games": sub["short_home_venue_n"].mean(),
                "mean_away_venue_games": sub["short_away_venue_n"].mean(),
                "pct_with_empty_venue_split": float((sub["thin_venue_sample"] == 0).mean()),
                "win_rate": float(sub["is_win"].mean()),
            }
        )
    return pd.DataFrame(rows)


def correlation_report(features: pd.DataFrame) -> dict:
    """Correlation between the feature's magnitude and how thin its inputs are."""
    thin = features["thin_venue_sample"].astype(float)
    mag = features["abs_div_delta"].astype(float)
    return {
        "corr_abs_delta_vs_venue_games": float(np.corrcoef(mag, thin)[0, 1]),
        "mean_abs_delta_empty_split": float(mag[thin == 0].mean()) if (thin == 0).any() else float("nan"),
        "mean_abs_delta_full_split": float(mag[thin >= 3].mean()) if (thin >= 3).any() else float("nan"),
    }


def effective_sample(features: pd.DataFrame) -> dict:
    """Games vs. slate-days. Games on one day are not independent draws."""
    days = features["game_date"].nunique()
    return {
        "games": len(features),
        "slate_days": days,
        "games_per_day": len(features) / days if days else float("nan"),
    }
