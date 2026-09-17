#!/usr/bin/env python3
"""
Walk-forward backtest of the total-bases divergence signal.

    python run_backtest.py --start 2023-04-01 --end 2025-09-30

Needs network access to statsapi.mlb.com. Boxscores are cached under
`.cache/`, so re-runs over the same dates are offline and free.
"""
from __future__ import annotations

import argparse

import pandas as pd

import backtest as bt
import features as feat


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="ISO start date, e.g. 2023-04-01")
    parser.add_argument("--end", required=True, help="ISO end date, e.g. 2025-09-30")
    parser.add_argument("--short-days", type=int, default=15)
    parser.add_argument("--long-days", type=int, default=60)
    parser.add_argument("--shrink-k", type=float, default=0.0,
                        help="empirical-Bayes weight in games; 0 reproduces the notebook")
    parser.add_argument("--strict-cutoff", action="store_true",
                        help="use first-pitch time rather than the calendar day as the history cutoff")
    parser.add_argument("--per-pa", action="store_true",
                        help="normalize total bases by plate appearances")
    parser.add_argument("--train-frac", type=float, default=0.6)
    parser.add_argument("--min-train-n", type=int, default=150)
    parser.add_argument("--null-p", type=float, default=bt.BREAK_EVEN_110,
                        help="null win rate; default is the -110 break-even")
    parser.add_argument("--csv", help="optional path to write per-game features")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    signal_cfg = feat.SignalConfig(
        short_days=args.short_days,
        long_days=args.long_days,
        strict_cutoff=args.strict_cutoff,
        shrink_k=args.shrink_k,
        per_pa=args.per_pa,
    )
    test_cfg = bt.BacktestConfig(
        train_frac=args.train_frac,
        min_train_n=args.min_train_n,
        null_p=args.null_p,
    )

    # Imported here so the module stays importable without network deps.
    import mlb_data

    print(f"loading games {args.start} -> {args.end} ...")
    games = mlb_data.load_games(args.start, args.end)
    print(f"  {len(games)} completed games with boxscores")

    feats = feat.build_features(games, signal_cfg)
    print(f"  {len(feats)} scored after the {signal_cfg.long_days}-day warm-up")
    if args.csv:
        feats.to_csv(args.csv, index=False)
        print(f"  features written to {args.csv}")

    report = bt.run(feats, test_cfg)

    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.float_format", lambda v: f"{v:,.4f}")

    print("\n" + "=" * 96)
    print("IN-SAMPLE SWEEP (descriptive only -- every threshold saw every game)")
    print("=" * 96)
    print(report["in_sample_sweep"].to_string(index=False))

    train_lo, train_hi = report["train_span"]
    test_lo, test_hi = report["test_span"]
    print("\n" + "=" * 96)
    print(f"TRAIN {train_lo.date()} -> {train_hi.date()}  ({report['train_n']} games)")
    print(f"TEST  {test_lo.date()} -> {test_hi.date()}  ({report['test_n']} games)")
    print(f"threshold selected on TRAIN only: |div_delta| >= {report['selected_threshold']:.2f}")
    print("=" * 96)

    oos = report["out_of_sample"]
    if oos.get("n", 0) == 0:
        print("no qualifying games in the test period")
        return

    print(f"  qualifying games      {oos['n']} ({oos['trigger_rate']:.1%} of test slate)")
    print(f"  record                {oos['wins']}-{oos['losses']}  ({oos['win_rate']:.1%})")
    print(f"  95% CI (day-block)    [{oos['ci95_low']:.1%}, {oos['ci95_high']:.1%}]")
    print(f"  p-value vs {test_cfg.null_p:.3f}    {oos['p_value']:.4f}")
    print(f"  net units at -110     {oos['net_units_at_110']:+.2f}u  (ROI {oos['roi_at_110']:+.1%})")
    print(f"  picked home           {oos['home_pick_rate']:.1%} of the time; "
          f"home teams won {oos['home_win_rate']:.1%} of these games")

    print("\nTEST-PERIOD BASELINES (same games, no threshold)")
    print(report["test_baselines"].to_string(index=False))
    print(
        "\nThe -110 columns are counterfactual: this signal picks a side without "
        "reading a price,\nso real moneylines would not be -110 on both teams. "
        "Treat them as a ranking aid, not a P&L."
    )


if __name__ == "__main__":
    main()
