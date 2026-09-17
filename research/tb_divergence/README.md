# Total-bases divergence — audit and walk-forward harness

Reference implementation and review of the notebook signal

```
Δ_div = Δ_60d − Δ_15d        bet home if Δ_div > 0, else away
```

where `Δ_w` is a home-minus-away total-bases rating computed over a `w`-day
window.

---

## 1. Verdict on the notebook

**The history windows are leak-free. The reported numbers are not
out-of-sample.** Those are different claims, and only the first one holds.

### 1.1 What is correct

| Check | Status |
|---|---|
| `date < m_date` window bound | Strict. No same-day or future game enters a feature. |
| Eval games used as history for *later* eval games | Legitimate walk-forward, not leakage. |
| Long-window warm-up | `history_start = earliest_eval − (60 + 5)d` covers every eval game's 60-day window. |
| `calc_tb` fallback | Correct. `hits` already counts each extra-base hit once, so doubles add 1, triples 2, homers 3. |
| Rating denominators | Correct and non-obvious. `a_allowed` is measured on home-team batting, so `/lg_h` is the right scale; `h_allowed` is away-team batting, so `/lg_a` is right. |
| `binomtest(..., alternative='greater')` | Used correctly. |
| Bins in cell 2 vs. sweep in cell 1 | Reconcile exactly — 100 games, 56 wins, cumulative counts match at every θ. |

`home_tb_allowed` and `away_tb_allowed` are exact aliases of `away_tb` and
`home_tb`. Harmless, but they are not independent data.

### 1.2 What invalidates the result

**a. The threshold was chosen on the same 100 games it is reported on.**
Eight thresholds were swept and the best one reported at p = 0.0200. Holm-adjusted
across the eight, that becomes **p = 0.16**:

| θ | Record | Win% | p raw | p Holm |
|---|---|---|---|---|
| 0.00 | 56-44 | 56.0% | 0.2678 | 0.4128 |
| 0.05 | 49-34 | 59.0% | 0.1354 | 0.4128 |
| 0.10 | 44-27 | 62.0% | 0.0667 | 0.4003 |
| **0.15** | **41-21** | **66.1%** | **0.0200** | **0.1603** |
| 0.20 | 32-16 | 66.7% | 0.0323 | 0.2261 |
| 0.25 | 26-15 | 63.4% | 0.1041 | 0.4128 |
| 0.30 | 20-10 | 66.7% | 0.0826 | 0.4128 |
| 0.35 | 17-8 | 68.0% | 0.0856 | 0.4128 |

Nothing in the sweep is significant. The Wilson 95% interval at θ = 0.15 is
**[53.7%, 76.7%]** against a 52.4% break-even — the lower bound is essentially
at break-even before any clustering adjustment.

**b. Cell 2 prices games with the test set.** `raw_bins` are the cell-1 results
transcribed. `EmpiricalTierPricer` then reports `P(Win) = 61.1%` for a game at
Δ = 0.17 — a number read off the same 14 games it is meant to predict. Every EV
and every Kelly stake downstream is in-sample.

**c. The 100 games are about 7 slate-days, not 100 independent trials.**
929 boxscores over ~65 days is 14.3 games/day, so `tail(100)` spans roughly one
week. Games on one slate share the league-average terms in the denominator and
often share teams. The effective sample is closer to the number of days than the
number of games, and the binomial test assumes the opposite.

**d. Flat −110 on both sides is fiction.** The signal picks a side without ever
reading a price. Real MLB moneylines run from about −250 to +200. A 66% win rate
is a loss if those picks average −180. Until it is graded against actual
moneylines — closing, ideally — the ROI column is not a measurement.

**e. The null of 0.524 conflates skill with profitability.** Testing against the
−110 break-even asks "is this profitable at a price you are not paying". To ask
whether the signal predicts at all, the null is the base rate of its own picks.
The harness reports both and adds `always_home`, `long_level`, `short_level` and
`fade_short` baselines — if `Δ_60 − Δ_15` cannot beat `Δ_60` alone, the
subtraction is adding nothing.

**f. `tail(100)` cuts a slate in half.** The frame is sorted by date only, with
no time component, so the boundary day splits on arbitrary row order. The cohort
is not reproducible across runs.

### 1.3 Logic problems independent of the validation

1. **Home-field is normalized out of the rating, then priced as if it were not.**
   Dividing by `lg_h` and `lg_a` removes the home-road split, so `Δ_div` is a
   pure team-quality measure — yet picks are graded at a flat price. Check
   `home_pick_rate`: if the signal leans home, part of the "edge" is home-field,
   which the market charges for.

2. **Thin venue splits inflate the feature.** Each club is measured only at the
   venue it is about to play, so a 15-day window holds ~6 home games — sometimes
   zero, after a road trip. An empty split falls back to the league mean, which
   pulls `Δ_15` toward zero and pushes `|Δ_div|` *up*. On pure-noise synthetic
   data this harness measures **corr(|Δ_div|, venue games) = −0.25**: high-threshold
   buckets systematically contain the games with the *least* evidence behind them.
   That does not by itself manufacture a win rate (noise data stays at ~50%), but
   it means θ is partly selecting for missing data rather than for divergence, so
   whatever the buckets correlate with in live data needs checking.
   `diagnostics.venue_sample_profile` reports this.

3. **The bottom of the ninth biases total bases against good home teams.** A home
   team that is winning does not bat in the ninth — roughly 11% fewer plate
   appearances — so `home_tb` is depressed precisely for teams that win at home,
   while `home_tb_allowed` is never truncated. Differencing two windows cancels
   this only if the team's home win rate is stable across both, which is exactly
   the thing the signal claims to detect a change in. `per_pa=True` removes it.
   Extra-inning games inflate both sides for the same structural reason.

4. **Ties bet the away side.** `div_delta > 0` sends every exact zero to the road
   team. Zeros are real when both windows fall back to league means. The harness
   flags them as `degenerate` and drops them by default.

5. **Boxscore failures are dropped silently.** `except Exception: valid = False`
   with no retry and no count. Dropped games thin the league averages that every
   rating divides by.

6. **The Kelly cap makes the tiers cosmetic.** At p ≥ 0.61 and b = 0.91,
   quarter-Kelly is ~4.6%, above the 4% cap — so Tier 1, Tier 2 and Tier 3 all
   stake exactly $400. The tiering does nothing.

7. **Two sources of truth for the same threshold.** `get_expected_win_prob`
   returns `"No Action"` for bucket index < 3 while `price_matchup` independently
   gates on `abs_delta < 0.15`. Re-cutting the bins silently desynchronizes them.

8. **The shrinkage is far too weak and its prior is unexplained.** `k_prior = 6`
   on a 14-game bin moves 64.3% to 61.1%. For bins this small the prior should
   dominate. The prior itself, 0.535, does not match the sweep's 0.524 null and
   has no stated source.

---

## 2. What this harness does differently

- **One cutoff, enforced once.** `features.history_view` finds both window
  bounds by binary search on sorted start times. No consumer re-derives "the
  past", so no consumer can get it wrong.
- **Real first-pitch timestamps.** Ingestion keeps `gameDate` (UTC), not the
  slate date, so doubleheaders order correctly. `strict_cutoff=True` lets an
  afternoon game inform the same evening's slate; `strict_cutoff=False`
  reproduces the notebook. Both are leak-free — the notebook's version just
  discards same-day information. Note the window slides rather than grows:
  moving the cutoff forward also moves the lower bound, so the strict mode
  changes *which* games are in the window, not how many.
- **Threshold selected on a training period, scored on a later one.**
  `backtest.split_by_day` splits on day boundaries so no slate straddles the
  split; `select_threshold` only ever sees the training half.
- **Day-block bootstrap intervals.** Whole slates are resampled, not individual
  games.
- **Baselines reported alongside.** `always_home`, `long_level`, `short_level`,
  `fade_short`.
- **Warm-up enforced.** The first `long_days` of the sample are excluded, so no
  game is scored on a truncated long window.
- **Corrections are opt-in and independent** — `shrink_k`, `per_pa`,
  `strict_cutoff` — so each one's effect is measurable on its own.

### Leakage is tested, not asserted

`tests/test_leakage.py` runs against deterministic synthetic seasons, across
four config combinations:

- `test_features_survive_poisoning_the_future` — every outcome from a date
  forward is replaced with absurd values; all earlier features must be
  bit-identical.
- `test_feature_matches_a_frame_truncated_at_the_game` — each feature equals
  what a live run holding only already-started games would produce.
- `test_history_window_never_reaches_the_cutoff` — no history row is at or
  after the cutoff, and the game never sees itself.
- `test_threshold_selection_never_reads_the_test_half` — flipping every
  held-out outcome must not change the selected θ.
- `test_warmup_period_is_excluded`, `test_split_keeps_days_whole_and_ordered`.

```bash
pip install -r requirements-research.txt
cd research/tb_divergence && python -m pytest tests/ -q
```

---

## 3. Running it

```bash
python run_backtest.py --start 2023-04-01 --end 2025-09-30
python run_backtest.py --start 2023-04-01 --end 2025-09-30 --strict-cutoff --shrink-k 6 --per-pa
```

Needs `statsapi.mlb.com`. Boxscores are cached under `.cache/`, so re-runs over
the same dates are offline.

**Use at least two full seasons.** One week of games cannot distinguish a 66%
edge from a 50% coin flip.

---

## 4. Before this becomes a per-game signal

1. Re-run on 2+ seasons and read only the out-of-sample block.
2. Grade against real closing moneylines. Everything above is side-picking
   accuracy; it is not a P&L until prices are attached.
3. Check whether it beats `long_level` on the same games. If not, the
   divergence term is not what is working.
4. Check `home_pick_rate` against the home win rate on the same games.
5. Re-fit the tier probabilities on the training period only, or drop the
   tiers — under a 4% cap they do not change a single stake.
