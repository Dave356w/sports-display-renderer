# ANALYSES — pre-registered evaluations

Registry for evaluation metrics on the lean ledger. New slices must be
registered here BEFORE being computed; anything not listed is exploratory
and gets no evidentiary weight.

## Vs-market evaluation (registered 2026-07-08)

- **Primary metrics:** vs-market z (wins minus market-expected wins,
  normalized) and flat ROI in units at DK closing moneylines. Win rate
  alone cannot distinguish model skill from favorite-picking; these two
  can. Computed by `market_backfill.vs_market_summary` per model
  (xwOBA lean full-game; platoon lean full-game, reliable-only).
- **Formal read:** at n≈200 graded games per model. No conclusions
  before that; interim numbers on grades.html are monitoring only.
- **Data:** DraftKings closing lines via ESPN core API, devigged two-way
  (`close_p_home`). Only settled games (`state == "post"`) ever receive
  `close_*` values; unjoined rows stay NaN and retry on the next run.
- **Baseline recorded at registration** (59 market-joined graded games,
  2026-07-02..07-07, repo ledger as source of truth):
  - xwOBA: 36-23, expected 32.6 W, z +0.91, flat ROI +4.04u,
    fav-agree 73%, fav baseline 34-25
  - platoon (reliable, n=45): 25-20, expected 24.8 W, z +0.05,
    flat ROI -1.72u, fav-agree 73%, fav baseline 27-18
- **No new slices** (terciles, home/away, favorite/dog, etc.) without
  registering them here first.
