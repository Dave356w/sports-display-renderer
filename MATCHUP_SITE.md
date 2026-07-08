# MLB Matchup Site + Grading Ledger

Daily pitcher-vs-lineup matchup leans, rendered as a static site and graded
against final scores in CI. This is the productionized port of
`notebooks/Shrunk_mlb_matchup_render_consolidated.ipynb`.

## Pieces

| File | Role |
|---|---|
| `build_site.py` | Fetches the day's slate + stats, builds the two matchup models, writes `public/index.html` and dumps `data/leans_{DATE}_xw.csv` / `_pl.csv` |
| `grade_leans.py` | Ingests the leans dumps into `data/mlb_lean_ledger.csv`, grades finished games (full-game + F5), prints/writes the record report |
| `.github/workflows/build.yml` | build → grade → commit ledger → deploy Pages, 3x daily + manual |

## The two models

- **xwOBA lens** (`leans_*_xw.csv`, from `matchup_df`) — Statcast contact
  quality via the log5 / odds-ratio method anchored on league average
  (`M = B·P/L`; EV/LA additive). The signal is `edge = M − L` per side;
  `edge_xwOBA(home offense) − edge_xwOBA(away offense)` drives the lean.
  Also carries the reliability-shrunk composite score (`comp_z`).
- **Platoon OPS lens** (`leans_*_pl.csv`, from `matchup_platoon_df`) — OPS
  vs pitcher hand from StatsAPI splits, per-batter odds-ratio with both
  sides' splits regressed toward (overall × league platoon multiplier).
  Thin-sample lines are flagged `reliable=False` and excluded from grading
  headline records.

Lineups come from Savant `gf?game_pk=` when posted, otherwise a projected
top-PA active-roster lineup (early builds are projected; the 11am/5pm
re-runs refresh pending ledger rows as real lineups post).

## CI flow (`build.yml`)

1. `python build_site.py` — builds `public/index.html`, dumps the day's leans
   to `data/`.
2. `python grade_leans.py` — ingests any un-ledgered `data/leans_*_xw.csv`
   into pending rows (re-runs on the same date refresh still-pending rows,
   handling SP scratches / lineup swaps up to first pitch; graded rows are
   never touched), then grades all pending rows via
   `schedule?hydrate=linescore` (full-game + first-5-innings; postponed /
   cancelled → void), and writes `data/ledger_report.txt`.
3. The ledger + report + leans dumps in `data/` are committed back to the
   repo (`contents: write`) so state survives between runs.
4. `public/` deploys to GitHub Pages (requires Pages source = GitHub Actions
   in the repo settings).

Schedule: ~3:15am ET (grades last night's games), ~11am ET (lineup refresh),
~5pm ET (final pre-slate build). All times drift 1h under EST.

The pre-existing `render.yml` (e-paper standings PNG) is independent and
untouched; both workflows commit to `main` and rebase before pushing.

## Local / manual runs

```bash
pip install -r requirements.txt
python build_site.py                      # today's slate (US/Eastern)
SLATE_DATE=2026-07-01 python build_site.py  # specific date
python grade_leans.py                     # ingest + grade + report
```

Env overrides: `SLATE_DATE`, `DATA_DIR` (default `data`), `SITE_DIR`
(default `public`), `CACHE_DIR` (default `.savant_cache`, gitignored),
`MODEL_TAG` (ledger model tag, default `xw+plat_consol_v1`).

## Ledger notes

- One row per `game_pk`; `status` walks pending → graded (or void).
- `xw_full` / `xw_f5` / `ops_full` / `ops_f5` are W/L/T grades for each
  lens (F5 allows ties; full game does not).
- Once ≥120 graded F5 decisions accumulate, the report fits
  `logit(home F5 win) ~ d_lineup + d_sp` to check the SP-vs-lineup
  weighting; symmetric log5 implies `b_sp/b_lineup ≈ +1`, and a stable
  departure is the reweight to apply (`net_w = d_lineup + w·d_sp`).
