#!/usr/bin/env python3
"""Headless runner: attach DK closing MLs to the grading ledger.

Usage
-----
Daily (after grading, before rendering grades.html):
    python run_market_update.py --ledger path/to/ledger.csv

One-time historical merge of the pre-enriched 59-game file:
    python run_market_update.py --ledger path/to/ledger.csv \
        --merge-backfill ledger_with_market.csv

Preview without writing:
    python run_market_update.py --ledger path/to/ledger.csv --dry-run

Exit codes: 0 ok · 1 validation/gate failure · 2 unexpected error.
Writes are atomic (temp file + os.replace). Re-running is a no-op by design.

If the ledger's column names differ from the defaults in
market_backfill.COL, pass overrides, e.g.:
    --col date=game_date --col away=away_abbr
"""

import argparse
import os
import sys
import tempfile

import numpy as np
import pandas as pd

import market_backfill as mb


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_ledger(path, col):
    if not os.path.exists(path):
        fail(f"ledger not found: {path}")
    df = pd.read_csv(path)
    missing = [v for v in col.values() if v not in df.columns]
    if missing:
        fail(f"ledger is missing expected columns {missing}. "
             f"Present: {list(df.columns)}. "
             f"Fix with --col key=actual_name (keys: {list(col.keys())}).")
    return df


def merge_backfill(df, backfill_path, col):
    """One-time merge of pre-enriched rows. Gated: only fills NaN market
    cells on exact (date, away, home, p_away) matches; never alters
    existing values; ledger row count must be unchanged."""
    if not os.path.exists(backfill_path):
        fail(f"backfill file not found: {backfill_path}")
    bf = pd.read_csv(backfill_path)
    need = [col["date"], col["away"], col["home"], col["p_away"]] + mb.MARKET_COLS
    miss = [c for c in need if c not in bf.columns]
    if miss:
        fail(f"backfill file missing columns: {miss}")

    key_cols_ledger = [col["date"], col["away"], col["home"], col["p_away"]]
    key = lambda frame, cols: list(zip(*(frame[c].astype(str) for c in cols)))
    bf_idx = {k: i for i, k in enumerate(key(bf, key_cols_ledger))}
    dup = len(bf) - len(bf_idx)
    if dup:
        fail(f"backfill file has {dup} duplicate join keys; refusing to merge")

    for c in mb.MARKET_COLS:
        if c not in df.columns:
            df[c] = np.nan

    n_before = len(df)
    snapshot = df.drop(columns=mb.MARKET_COLS).copy()
    filled = 0
    for i, k in zip(df.index, key(df, key_cols_ledger)):
        j = bf_idx.get(k)
        if j is None:
            continue
        for c in mb.MARKET_COLS:
            cur = df.at[i, c]
            new = bf.at[j, c]
            if pd.isna(new):
                continue
            if pd.notna(cur):
                if str(cur) != str(new):
                    fail(f"row {i} col {c}: existing value {cur!r} != backfill "
                         f"{new!r}; graded market data is immutable")
                continue
            df.at[i, c] = new
        filled += 1

    # gates
    if len(df) != n_before:
        fail("merge changed ledger row count")
    if not snapshot.equals(df.drop(columns=mb.MARKET_COLS)):
        fail("merge modified non-market columns; aborting")
    print(f"backfill merge: market data applied to {filled} rows "
          f"({len(bf)} rows in backfill file)")
    if filled != len(bf):
        print(f"  note: {len(bf) - filled} backfill rows had no ledger match "
              f"(check join keys)", file=sys.stderr)
    return df


def atomic_write(df, path):
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".csv")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", required=True, help="path to ledger CSV (read+write)")
    ap.add_argument("--merge-backfill", metavar="CSV",
                    help="one-time merge of a pre-enriched ledger file")
    ap.add_argument("--col", action="append", default=[], metavar="KEY=NAME",
                    help="override a COL mapping entry (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="no write-back")
    args = ap.parse_args()

    col = dict(mb.COL)
    for kv in args.col:
        if "=" not in kv:
            fail(f"--col expects KEY=NAME, got {kv!r}")
        k, v = kv.split("=", 1)
        if k not in col:
            fail(f"unknown COL key {k!r}; valid: {list(col)}")
        col[k] = v

    df = load_ledger(args.ledger, col)
    n0, cells0 = len(df), df.notna().sum().sum()

    if args.merge_backfill:
        df = merge_backfill(df, args.merge_backfill, col)

    df = mb.attach_market(df, col=col)
    skips = df.attrs.get("market_skips", [])

    # invariants
    if len(df) != n0:
        fail("row count changed during update")
    pending = df[df[col["away_runs"]].isna() | df[col["home_runs"]].isna()]
    if pending["close_home_ml"].notna().any():
        fail("close values present on pending rows — lookahead violation")

    print()
    mb.vs_market_summary(df, col=col)

    if args.dry_run:
        print("\ndry-run: ledger NOT written")
    else:
        atomic_write(df, args.ledger)
        print(f"\nwrote {args.ledger} ({len(df)} rows)")

    if skips:
        print(f"{len(skips)} rows skipped this run (will retry next run):",
              file=sys.stderr)
        for i, why in skips:
            print(f"  row {i}: {why}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"UNEXPECTED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
