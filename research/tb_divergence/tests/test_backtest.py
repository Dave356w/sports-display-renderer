"""End-to-end checks on the evaluation layer."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest as bt  # noqa: E402
import diagnostics as diag  # noqa: E402
import features as feat  # noqa: E402
from tests.synthetic import make_season  # noqa: E402


@pytest.fixture(scope="module")
def noise_features():
    """Outcomes independent of team identity, so no real edge exists."""
    return feat.build_features(make_season(n_days=220, seed=11), feat.SignalConfig())


def test_holm_adjustment_matches_worked_example():
    raw = [0.01, 0.04, 0.03]
    # Sorted: .01 (x3 = .03), .03 (x2 = .06), .04 (x1 = .04 -> held at .06).
    assert bt.holm_adjust(raw) == pytest.approx([0.03, 0.06, 0.06])


def test_holm_is_monotone_and_never_shrinks_a_pvalue():
    raw = [0.2678, 0.1354, 0.0667, 0.0200, 0.0323, 0.1041, 0.0826, 0.0856]
    adjusted = bt.holm_adjust(raw)
    assert all(a >= r for a, r in zip(adjusted, raw))
    assert max(adjusted) <= 1.0


def test_sweep_buckets_reconcile_with_cumulative_counts(noise_features):
    cfg = bt.BacktestConfig()
    table = bt.sweep(noise_features, cfg).set_index("threshold")
    for lower, upper in zip(cfg.thresholds, cfg.thresholds[1:]):
        band = noise_features[
            (noise_features["abs_div_delta"] >= lower)
            & (noise_features["abs_div_delta"] < upper)
        ]
        assert table.loc[lower, "n"] - table.loc[upper, "n"] == len(band)
        assert table.loc[lower, "wins"] - table.loc[upper, "wins"] == band["is_win"].sum()


def test_run_reports_out_of_sample_after_the_training_span(noise_features):
    cfg = bt.BacktestConfig(min_train_n=50, bootstrap_draws=300)
    report = bt.run(noise_features, cfg)

    assert report["train_span"][1] < report["test_span"][0]
    assert report["selected_threshold"] in cfg.thresholds
    assert report["train_n"] + report["test_n"] == report["n_games"]

    oos = report["out_of_sample"]
    assert 0.0 <= oos["win_rate"] <= 1.0
    assert oos["ci95_low"] <= oos["win_rate"] <= oos["ci95_high"]


def test_no_edge_is_found_on_noise(noise_features):
    """The harness must not manufacture significance where none exists."""
    report = bt.run(noise_features, bt.BacktestConfig(min_train_n=50, bootstrap_draws=300))
    assert report["out_of_sample"]["p_value"] > 0.05


def test_selection_refuses_to_pick_an_underpowered_bucket(noise_features):
    train, _ = bt.split_by_day(noise_features, 0.6)
    with pytest.raises(ValueError, match="min_train_n"):
        bt.select_threshold(train, bt.BacktestConfig(min_train_n=10**6))


def test_degenerate_rows_are_dropped_before_scoring():
    frame = feat.build_features(make_season(n_days=140, seed=5), feat.SignalConfig())
    forced = frame.copy()
    forced.loc[forced.index[:20], "degenerate"] = True
    report = bt.run(forced, bt.BacktestConfig(min_train_n=50, bootstrap_draws=100))
    assert report["dropped_degenerate"] >= 20
    assert report["n_games"] == len(forced) - report["dropped_degenerate"]


def test_thin_venue_samples_inflate_the_feature(noise_features):
    """The construction artifact described in the README, asserted on noise."""
    report = diag.correlation_report(noise_features)
    # Bigger |div_delta| goes with fewer venue-specific games behind it.
    assert report["corr_abs_delta_vs_venue_games"] < -0.1
