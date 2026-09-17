"""The claims the signal stands on: nothing from the present or future leaks in."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest as bt  # noqa: E402
import features as sig  # noqa: E402
from tests.synthetic import make_season, poison_from  # noqa: E402

CONFIGS = [
    sig.SignalConfig(),
    sig.SignalConfig(strict_cutoff=True),
    sig.SignalConfig(strict_cutoff=True, shrink_k=4.0),
    sig.SignalConfig(strict_cutoff=True, per_pa=True),
]


@pytest.mark.parametrize("cfg", CONFIGS)
def test_history_window_never_reaches_the_cutoff(cfg):
    games = sig.validate_games(make_season(n_days=90), cfg)
    starts = games["start_utc"].dt.tz_convert("UTC").dt.tz_localize(None).values

    for row in games.itertuples(index=False):
        cutoff = sig.cutoff_for(row.start_utc, cfg)
        for window in (cfg.short_days, cfg.long_days):
            hist = sig.history_view(starts, games, row.start_utc, window, cfg)
            assert (hist["start_utc"] < cutoff).all()
            assert (hist["start_utc"] >= cutoff - pd.Timedelta(days=window)).all()
            assert row.game_pk not in set(hist["game_pk"])


@pytest.mark.parametrize("cfg", CONFIGS)
def test_features_survive_poisoning_the_future(cfg):
    """Corrupt every game from a date forward; earlier features must not move."""
    games = make_season(n_days=110)
    clean = sig.build_features(games, cfg).set_index("game_pk")

    cutoff = pd.Timestamp("2025-06-15", tz="UTC")
    poisoned = sig.build_features(poison_from(games, cutoff), cfg).set_index("game_pk")

    before = clean[clean["start_utc"] < cutoff]
    assert len(before) > 50, "test needs a meaningful number of pre-cutoff games"

    for column in ("delta_short", "delta_long", "div_delta"):
        np.testing.assert_allclose(
            before[column].values,
            poisoned.loc[before.index, column].values,
            rtol=0,
            atol=0,
        )


@pytest.mark.parametrize("cfg", CONFIGS)
def test_feature_matches_a_frame_truncated_at_the_game(cfg):
    """Each feature equals what a live run with no later data would produce."""
    games = make_season(n_days=90)
    full = sig.build_features(games, cfg).set_index("game_pk")

    rng = np.random.default_rng(3)
    sample = rng.choice(full.index.values, size=8, replace=False)

    for game_pk in sample:
        target = full.loc[game_pk]
        # Everything that had started, plus the game itself. This is exactly
        # what would exist at first pitch.
        visible = games[
            (games["start_utc"] < target["start_utc"]) | (games["game_pk"] == game_pk)
        ]
        live = sig.build_features(visible, cfg, require_full_warmup=False).set_index("game_pk")
        assert live.loc[game_pk, "div_delta"] == pytest.approx(target["div_delta"], abs=1e-12)


def test_strict_cutoff_sees_earlier_same_day_games_and_day_cutoff_does_not():
    """Both modes are leak-free; the day cutoff just discards same-day games.

    Note the windows slide rather than grow: moving the cutoff forward by a few
    hours also moves the lower bound, so `long_n` is not reliably larger. The
    difference is in *which* games are in the window, tested here directly.
    """
    games = make_season(n_days=100)
    modes = {
        "day": sig.SignalConfig(strict_cutoff=False),
        "strict": sig.SignalConfig(strict_cutoff=True),
    }
    prepared, starts = {}, {}
    for name, cfg in modes.items():
        frame = sig.validate_games(games, cfg)
        prepared[name] = frame
        starts[name] = frame["start_utc"].dt.tz_convert("UTC").dt.tz_localize(None).values

    evening = prepared["strict"][prepared["strict"]["start_utc"].dt.hour == 23]
    evening = evening[evening["start_utc"] > games["start_utc"].min() + pd.Timedelta(days=70)]
    assert len(evening) > 50

    checked = 0
    for row in evening.head(40).itertuples(index=False):
        day = row.start_utc.normalize()
        afternoon_pks = set(
            games[(games["start_utc"] >= day) & (games["start_utc"] < row.start_utc)]["game_pk"]
        )
        assert afternoon_pks, "fixture should have earlier games on the same day"

        strict_hist = sig.history_view(
            starts["strict"], prepared["strict"], row.start_utc, modes["strict"].long_days, modes["strict"]
        )
        day_hist = sig.history_view(
            starts["day"], prepared["day"], row.start_utc, modes["day"].long_days, modes["day"]
        )
        strict_pks, day_pks = set(strict_hist["game_pk"]), set(day_hist["game_pk"])

        assert afternoon_pks <= strict_pks
        assert not (afternoon_pks & day_pks)
        # Neither mode may see the game itself or anything starting later.
        for pks, hist in ((strict_pks, strict_hist), (day_pks, day_hist)):
            assert row.game_pk not in pks
            assert (hist["start_utc"] < row.start_utc).all()
        checked += 1
    assert checked == 40


def test_warmup_period_is_excluded():
    games = make_season(n_days=90)
    cfg = sig.SignalConfig(long_days=60)
    feats = sig.build_features(games, cfg)

    data_start = games["start_utc"].min()
    assert feats["start_utc"].min() >= data_start + pd.Timedelta(days=60)
    # Every scored game has a fully-populated long window.
    assert (feats["long_n"] > 0).all()


def test_threshold_selection_never_reads_the_test_half():
    games = make_season(n_days=200)
    feats = sig.build_features(games, sig.SignalConfig())
    cfg = bt.BacktestConfig(min_train_n=50, bootstrap_draws=200)

    chosen, _ = bt.select_threshold(*_train_and_cfg(feats, cfg))

    # Flip every outcome in the held-out half. The choice must not budge.
    train, test = bt.split_by_day(feats, cfg.train_frac)
    scrambled = feats.copy()
    test_mask = scrambled["game_pk"].isin(test["game_pk"])
    scrambled.loc[test_mask, "is_win"] = 1 - scrambled.loc[test_mask, "is_win"]

    scrambled_train, _ = bt.split_by_day(scrambled, cfg.train_frac)
    rechosen, _ = bt.select_threshold(scrambled_train, cfg)
    assert chosen == rechosen


def _train_and_cfg(feats, cfg):
    train, _ = bt.split_by_day(feats, cfg.train_frac)
    return train, cfg


def test_split_keeps_days_whole_and_ordered():
    games = make_season(n_days=150)
    feats = sig.build_features(games, sig.SignalConfig())
    train, test = bt.split_by_day(feats, 0.6)

    assert len(train) and len(test)
    assert train["game_date"].max() < test["game_date"].min()
    assert set(train["game_pk"]).isdisjoint(test["game_pk"])
    assert len(train) + len(test) == len(feats)
