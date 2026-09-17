"""
Total-bases divergence signal (Delta_60d - Delta_15d), computed leak-free.

The feature for a game is built only from games that had already *finished*
before that game started. The cutoff is enforced in one place -- `history_view`
-- so every consumer inherits the guarantee instead of re-deriving it.

Schema expected by every function here (one row per completed game):

    game_pk    int     unique game id
    start_utc  datetime64[ns, UTC]  scheduled first pitch
    home_id    int
    away_id    int
    home_tb    float   total bases recorded by the home team
    away_tb    float   total bases recorded by the away team
    home_pa    float   home plate appearances (only needed when per_pa=True)
    away_pa    float   away plate appearances (only needed when per_pa=True)
    home_won   int     1 if the home team won

Note on the original notebook's `home_tb_allowed` / `away_tb_allowed` columns:
they are exact aliases of `away_tb` / `home_tb`, so they are not carried here.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = (
    "game_pk",
    "start_utc",
    "home_id",
    "away_id",
    "home_tb",
    "away_tb",
    "home_won",
)


@dataclass(frozen=True)
class SignalConfig:
    """Knobs for the divergence feature.

    The defaults reproduce the notebook. `strict_cutoff`, `shrink_k` and
    `per_pa` are the corrections; each can be toggled independently so the
    effect of every change is measurable on its own.
    """

    short_days: int = 15
    long_days: int = 60
    # True  -> history is every game that started before this game's first pitch
    #          (an afternoon game informs the same evening's slate).
    # False -> history stops at midnight UTC of the game's calendar day, which
    #          is what the notebook did. Both are leak-free; False just
    #          discards same-day information.
    strict_cutoff: bool = False
    # Empirical-Bayes weight, in games, pulling a team's window mean toward the
    # league mean. 0.0 reproduces the notebook's raw mean.
    shrink_k: float = 0.0
    # Normalize total bases by plate appearances. Removes the bias where a
    # home team that wins does not bat in the bottom of the ninth.
    per_pa: bool = False

    def __post_init__(self) -> None:
        if self.short_days <= 0 or self.long_days <= 0:
            raise ValueError("window lengths must be positive")
        if self.short_days >= self.long_days:
            raise ValueError("short_days must be shorter than long_days")
        if self.shrink_k < 0:
            raise ValueError("shrink_k must be non-negative")


def validate_games(games: pd.DataFrame, cfg: SignalConfig | None = None) -> pd.DataFrame:
    """Check the schema and return the frame sorted by start time."""
    missing = [c for c in REQUIRED_COLUMNS if c not in games.columns]
    if missing:
        raise ValueError(f"games frame is missing columns: {missing}")
    if cfg is not None and cfg.per_pa:
        missing_pa = [c for c in ("home_pa", "away_pa") if c not in games.columns]
        if missing_pa:
            raise ValueError(f"per_pa=True requires columns: {missing_pa}")
    if games["game_pk"].duplicated().any():
        raise ValueError("games frame contains duplicate game_pk values")
    if not pd.api.types.is_datetime64_any_dtype(games["start_utc"]):
        raise ValueError("start_utc must be datetime64")
    if games["start_utc"].isna().any():
        raise ValueError("start_utc contains nulls")
    return games.sort_values("start_utc", kind="mergesort").reset_index(drop=True)


def cutoff_for(start_utc: pd.Timestamp, cfg: SignalConfig) -> pd.Timestamp:
    """The exclusive upper bound of a game's history window.

    This is the single point where "what counts as the past" is decided.
    """
    if cfg.strict_cutoff:
        return start_utc
    return start_utc.normalize()


def history_view(
    sorted_starts: np.ndarray,
    games: pd.DataFrame,
    start_utc: pd.Timestamp,
    window_days: int,
    cfg: SignalConfig,
) -> pd.DataFrame:
    """Rows in [cutoff - window_days, cutoff), where cutoff <= start_utc.

    `sorted_starts` must be `games["start_utc"].values` with `games` already
    sorted ascending; both bounds are found by binary search, so no row at or
    after the cutoff can enter the slice by construction.
    """
    hi_ts = cutoff_for(start_utc, cfg)
    lo_ts = hi_ts - pd.Timedelta(days=window_days)
    lo = _index_of(sorted_starts, lo_ts)
    hi = _index_of(sorted_starts, hi_ts)
    return games.iloc[lo:hi]


def _index_of(sorted_starts: np.ndarray, moment: pd.Timestamp) -> int:
    """First position whose start time is >= `moment`."""
    naive = moment.tz_convert("UTC").tz_localize(None)
    return int(np.searchsorted(sorted_starts, np.datetime64(naive), side="left"))


def prepare_values(games: pd.DataFrame, cfg: SignalConfig) -> pd.DataFrame:
    """Attach the per-side quantity the ratings average over.

    Done once per frame rather than once per game: the windowed slices then
    carry the columns with them, so `compute_delta` never copies.
    """
    if cfg.per_pa:
        return games.assign(
            _home_val=games["home_tb"] / games["home_pa"],
            _away_val=games["away_tb"] / games["away_pa"],
        )
    return games.assign(_home_val=games["home_tb"], _away_val=games["away_tb"])


def _shrunk_mean(values: pd.Series, league_mean: float, k: float) -> float:
    """Team mean pulled toward the league mean by k pseudo-games."""
    n = len(values)
    if n == 0:
        return league_mean
    total = float(values.sum())
    return (total + k * league_mean) / (n + k)


def compute_delta(
    history: pd.DataFrame,
    home_id: int,
    away_id: int,
    cfg: SignalConfig,
) -> float:
    """Home-minus-away offense/defense rating over one history window.

    Each club is measured only at the venue it is about to play: the home team
    from its home games, the away team from its road games. Dividing by the
    matching league split (lg_home / lg_away) puts both ratings on a common
    scale, which also means home-field advantage is normalized *out* of the
    returned difference.
    """
    if history.empty:
        return 0.0

    if "_home_val" not in history.columns:
        history = prepare_values(history, cfg)
    home_col, away_col = "_home_val", "_away_val"

    lg_home = float(history[home_col].mean())
    lg_away = float(history[away_col].mean())
    if not (np.isfinite(lg_home) and np.isfinite(lg_away)) or lg_home <= 0 or lg_away <= 0:
        return 0.0

    home_games = history[history["home_id"] == home_id]
    away_games = history[history["away_id"] == away_id]

    k = cfg.shrink_k
    # What the home team does at home, and what the away team allows on the road.
    home_scored = _shrunk_mean(home_games[home_col], lg_home, k)
    away_allowed = _shrunk_mean(away_games[home_col], lg_home, k)
    # What the away team does on the road, and what the home team allows at home.
    away_scored = _shrunk_mean(away_games[away_col], lg_away, k)
    home_allowed = _shrunk_mean(home_games[away_col], lg_away, k)

    home_rating = (home_scored + away_allowed) / lg_home
    away_rating = (away_scored + home_allowed) / lg_away
    return float(home_rating - away_rating)


def build_features(
    games: pd.DataFrame,
    cfg: SignalConfig | None = None,
    require_full_warmup: bool = True,
) -> pd.DataFrame:
    """Compute the divergence feature for every game that has a usable history.

    With `require_full_warmup` the first `long_days` of the sample are dropped
    from the output: their long window is truncated by the start of the data,
    which silently rescales the feature rather than merely adding noise.
    """
    cfg = cfg or SignalConfig()
    games = prepare_values(validate_games(games, cfg), cfg)
    starts = games["start_utc"].dt.tz_convert("UTC").dt.tz_localize(None).values

    data_start = games["start_utc"].iloc[0]
    warmup_until = data_start + pd.Timedelta(days=cfg.long_days)

    rows = []
    for row in games.itertuples(index=False):
        if require_full_warmup and cutoff_for(row.start_utc, cfg) < warmup_until:
            continue

        short_hist = history_view(starts, games, row.start_utc, cfg.short_days, cfg)
        long_hist = history_view(starts, games, row.start_utc, cfg.long_days, cfg)

        # How much venue-specific evidence the short window actually had. When
        # these are 0 the team mean falls back to the league mean, which pushes
        # delta_short toward 0 and inflates |div_delta| for reasons that have
        # nothing to do with form. Exposed so the backtest can check for it.
        short_home_n = int((short_hist["home_id"] == row.home_id).sum())
        short_away_n = int((short_hist["away_id"] == row.away_id).sum())

        delta_short = compute_delta(short_hist, row.home_id, row.away_id, cfg)
        delta_long = compute_delta(long_hist, row.home_id, row.away_id, cfg)
        div_delta = delta_long - delta_short

        # A zero feature carries no directional information; the notebook still
        # bet the away side on it. Those rows are kept but flagged so the
        # backtest can drop them instead of scoring a coin flip as a pick.
        pick_home = bool(div_delta > 0)
        rows.append(
            {
                "game_pk": row.game_pk,
                "start_utc": row.start_utc,
                "game_date": row.start_utc.tz_convert("UTC").normalize(),
                "home_id": row.home_id,
                "away_id": row.away_id,
                "delta_short": delta_short,
                "delta_long": delta_long,
                "div_delta": div_delta,
                "abs_div_delta": abs(div_delta),
                "degenerate": div_delta == 0.0,
                "short_n": len(short_hist),
                "long_n": len(long_hist),
                "short_home_venue_n": short_home_n,
                "short_away_venue_n": short_away_n,
                "thin_venue_sample": int(min(short_home_n, short_away_n)),
                "pick_home": pick_home,
                "home_won": int(row.home_won),
                "is_win": int(pick_home == bool(row.home_won)),
            }
        )

    return pd.DataFrame(rows, columns=[
        "game_pk", "start_utc", "game_date", "home_id", "away_id",
        "delta_short", "delta_long", "div_delta", "abs_div_delta", "degenerate",
        "short_n", "long_n", "short_home_venue_n", "short_away_venue_n",
        "thin_venue_sample", "pick_home", "home_won", "is_win",
    ])
