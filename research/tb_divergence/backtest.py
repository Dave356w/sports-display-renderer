"""
Walk-forward evaluation for the divergence signal.

Two things the notebook's sweep did not do, and which change the conclusion:

1. The threshold is chosen on a training period and scored on a later,
   untouched period. Sweeping eight thresholds over one sample and reporting
   the best p-value measures the sweep, not the signal.
2. Uncertainty is computed by resampling whole slate-days, not individual
   games. Games on the same day share the feature's league-average terms and
   often a team, so they are not independent draws.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

# Break-even win rate for a -110 price on both sides.
BREAK_EVEN_110 = 0.5238095238095238
DEFAULT_THRESHOLDS = (0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35)


@dataclass(frozen=True)
class BacktestConfig:
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS
    # Minimum qualifying games before a threshold is eligible for selection.
    min_train_n: int = 150
    # Fraction of the *days* in the sample used to pick the threshold.
    train_frac: float = 0.6
    # Null win rate for the significance test. BREAK_EVEN_110 asks "is this
    # profitable at -110"; a base rate asks "does this predict at all".
    null_p: float = BREAK_EVEN_110
    # Score only games where the feature actually points somewhere.
    drop_degenerate: bool = True
    bootstrap_draws: int = 5000
    seed: int = 20260917


def _flat_110_units(wins: int, losses: int) -> float:
    """Net units risking 1.10 to win 1.00 on every play."""
    return wins * 1.0 - losses * 1.10


def holm_adjust(pvalues: list[float]) -> list[float]:
    """Holm-Bonferroni step-down adjusted p-values, in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        value = min(1.0, (m - rank) * pvalues[idx])
        running = max(running, value)
        adjusted[idx] = running
    return adjusted


def day_block_bootstrap(
    frame: pd.DataFrame,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    """95% CI for the win rate, resampling whole slate-days with replacement."""
    if frame.empty:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    by_day = [g["is_win"].values for _, g in frame.groupby("game_date", sort=True)]
    n_days = len(by_day)
    rates = np.empty(draws, dtype=float)
    for i in range(draws):
        picks = rng.integers(0, n_days, size=n_days)
        sample = np.concatenate([by_day[j] for j in picks])
        rates[i] = sample.mean()
    return (float(np.quantile(rates, 0.025)), float(np.quantile(rates, 0.975)))


def evaluate(frame: pd.DataFrame, threshold: float, null_p: float) -> dict:
    """Score one threshold on one set of games."""
    sub = frame[frame["abs_div_delta"] >= threshold]
    n = len(sub)
    if n == 0:
        return {"threshold": threshold, "n": 0}
    wins = int(sub["is_win"].sum())
    losses = n - wins
    net = _flat_110_units(wins, losses)
    return {
        "threshold": threshold,
        "n": n,
        "trigger_rate": n / len(frame) if len(frame) else float("nan"),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n,
        "home_pick_rate": float(sub["pick_home"].mean()),
        "home_win_rate": float(sub["home_won"].mean()),
        "net_units_at_110": net,
        "roi_at_110": net / (n * 1.10),
        "p_value": float(stats.binomtest(wins, n, p=null_p, alternative="greater").pvalue),
    }


def sweep(frame: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """Full-sample sweep. In-sample by construction -- for description only."""
    rows = [evaluate(frame, t, cfg.null_p) for t in cfg.thresholds]
    rows = [r for r in rows if r.get("n", 0) > 0]
    out = pd.DataFrame(rows)
    if not out.empty:
        out["p_holm"] = holm_adjust(out["p_value"].tolist())
    return out


def select_threshold(train: pd.DataFrame, cfg: BacktestConfig) -> tuple[float, pd.DataFrame]:
    """Pick the threshold on training data alone.

    Selection maximizes ROI among thresholds that clear `min_train_n`, so a
    12-game bucket at 75% cannot win the search. Ties break toward the lower
    threshold, which keeps more games and is the less aggressive choice.
    """
    table = sweep(train, cfg)
    eligible = table[table["n"] >= cfg.min_train_n]
    if eligible.empty:
        raise ValueError(
            f"no threshold reaches min_train_n={cfg.min_train_n} "
            f"(largest bucket was {int(table['n'].max()) if not table.empty else 0})"
        )
    best = eligible.sort_values(["roi_at_110", "threshold"], ascending=[False, True]).iloc[0]
    return float(best["threshold"]), table


def split_by_day(frame: pd.DataFrame, train_frac: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split on day boundaries, so no day straddles the split."""
    days = np.sort(frame["game_date"].unique())
    if len(days) < 2:
        raise ValueError("need at least two distinct game days to split")
    cut_idx = max(1, min(len(days) - 1, int(round(len(days) * train_frac))))
    split_day = days[cut_idx]
    train = frame[frame["game_date"] < split_day]
    test = frame[frame["game_date"] >= split_day]
    return train, test


def baselines(frame: pd.DataFrame, null_p: float) -> pd.DataFrame:
    """What the signal has to beat.

    `always_home` is the only free bet in baseball. `long_level` and
    `short_level` bet the rating levels the divergence is built from -- if the
    divergence has no edge over them, the subtraction is adding nothing.
    """
    variants = {
        "always_home": frame["home_won"].astype(int),
        "long_level": ((frame["delta_long"] > 0) == frame["home_won"].astype(bool)).astype(int),
        "short_level": ((frame["delta_short"] > 0) == frame["home_won"].astype(bool)).astype(int),
        "fade_short": ((frame["delta_short"] < 0) == frame["home_won"].astype(bool)).astype(int),
        "divergence": frame["is_win"],
    }
    rows = []
    for name, wins_series in variants.items():
        n = len(wins_series)
        if n == 0:
            continue
        wins = int(wins_series.sum())
        rows.append(
            {
                "strategy": name,
                "n": n,
                "wins": wins,
                "win_rate": wins / n,
                "net_units_at_110": _flat_110_units(wins, n - wins),
                "p_value": float(stats.binomtest(wins, n, p=null_p, alternative="greater").pvalue),
            }
        )
    return pd.DataFrame(rows)


def run(features: pd.DataFrame, cfg: BacktestConfig | None = None) -> dict:
    """Full walk-forward report.

    Returns the in-sample sweep (labelled as such), the threshold chosen on the
    training half, and that threshold's performance on the held-out half.
    """
    cfg = cfg or BacktestConfig()
    frame = features.copy()
    dropped_degenerate = 0
    if cfg.drop_degenerate:
        dropped_degenerate = int(frame["degenerate"].sum())
        frame = frame[~frame["degenerate"]]
    frame = frame.sort_values("start_utc", kind="mergesort").reset_index(drop=True)

    train, test = split_by_day(frame, cfg.train_frac)
    threshold, train_table = select_threshold(train, cfg)

    oos = evaluate(test, threshold, cfg.null_p)
    oos_sub = test[test["abs_div_delta"] >= threshold]
    lo, hi = day_block_bootstrap(oos_sub, cfg.bootstrap_draws, cfg.seed)
    oos["ci95_low"], oos["ci95_high"] = lo, hi

    return {
        "config": cfg,
        "n_games": len(frame),
        "dropped_degenerate": dropped_degenerate,
        "train_span": (train["game_date"].min(), train["game_date"].max()),
        "test_span": (test["game_date"].min(), test["game_date"].max()),
        "train_n": len(train),
        "test_n": len(test),
        "in_sample_sweep": sweep(frame, cfg),
        "train_sweep": train_table,
        "selected_threshold": threshold,
        "out_of_sample": oos,
        "test_baselines": baselines(test, cfg.null_p),
    }
