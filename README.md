# Sports Display Renderer

Renderer for the 7.3-inch e-paper sports collectible display.

Composites pennant artwork (`assets/*_.png`,
2172×724 source PNGs, white backgrounds flood-filled to alpha=0) onto a
parchment NL West Standings background (`assets/background_.png`, 971×1619),
then overlays the current date and live W-L / GB standings fetched from
the MLB Stats API.

## Live output

![NL West Standings](https://raw.githubusercontent.com/Dave356w/sports-display-renderer/main/public/mlb_nl_west.png)

## Setup

```bash
pip install -r requirements.txt
python render.py
```

Output is written to `public/mlb_nl_west.png`.

## License

The code in this repository is released under the [MIT License](LICENSE).

The team pennant artwork (`assets/*_.png`) is excluded from this license and is
intended for personal, non-commercial use only. MLB team names and logos are
trademarks of their respective clubs.

---

## Wheel Bias Tracker (`wheel-bias/`)

A mobile-first PWA for detecting and betting into mechanical bias on an American roulette wheel (38 pockets: 0, 00, 1–36).

**Live app:** https://dave356w.github.io/wheel-bias/

### Theory

#### Why arcs?

A mechanically biased wheel doesn't favour a single pocket — friction, wear, and fret deformation distribute the elevation across a physically contiguous arc of adjacent pockets. Detecting a single-pocket elevation at 38:1 against background noise would require thousands of spins. Detecting an arc of 5–9 pockets elevated together requires far fewer because the signal-to-noise ratio scales with arc size.

#### Recency weighting

Raw spin counts treat a result from 300 spins ago the same as one from 5 spins ago. Mechanical wheel conditions drift — a warped fret today may not match the wheel's state a week ago. All hit-rate calculations use exponentially decaying weights:

```
weight(age) = 2^(-age / HALFLIFE)
```

`HALFLIFE = 75` means a spin 75 ago counts half as much as the most recent one. The effective sample size (`nEff`) is computed from the sum of weights squared over sum-squared, giving a statistically valid denominator for z-score calculations despite the non-uniform weighting.

#### Circular window scan

The 38-pocket wheel is treated as a circular frequency string. The detector tests every contiguous window of size 3–9 at every starting position — 38 × 7 = **266 candidate windows** per evaluation. For each window:

1. Compute the recency-weighted observed hit rate `p_actual`
2. Compare to the fair-wheel expected rate `p_expected = size / 38`
3. Compute z-score using `nEff` as the effective sample size:

```
SE  = sqrt(p_expected × (1 - p_expected) / nEff)
z   = (p_actual - p_expected) / SE
```

4. Score each window as `z × bias%` (combines significance with magnitude)
5. Select the highest-scoring window as the **primary arc**

After finding the primary arc, its pockets are masked and the scan repeats on the unmasked remainder to find an independent **secondary arc**.

#### False positive correction

Scanning 266 windows post-hoc inflates apparent signal — the best window out of 266 random trials will look elevated even on a perfectly fair wheel. Gates are calibrated empirically:

| Spin count | Min bias | Min z | Approx FP rate |
|-----------|---------|-------|----------------|
| < 75      | 22%     | 3.0   | ~3%            |
| 75–149    | 15%     | 3.0   | ~1%            |
| 150+      | 12%     | 3.0   | ~3.5%          |

The z ≥ 3.0 threshold is constant — a single-test z of 3.0 corresponds to p < 0.003, which is tight enough to stay below 5% false positives even after the scan's multiple-comparison inflation.

#### Portfolio — straight arc cover

Once a qualifying arc is confirmed, the play is pure and simple: **one straight-up chip on each arc pocket** at 35:1. There are no inside primaries, outside bets, or multi-layer structures. Every unit staked goes onto a pocket with a confirmed bias reading, undiluted by covering numbers outside the arc.

Pockets are ranked by recency-weighted heat and capped at 9 chips. They are displayed in physical wheel order for easy placement.

The play only fires if:
- Observed weighted probability ≥ break-even hit rate × a sample-size multiplier
- Portfolio edge > 0: `E = (36 × p_arc_pockets / stake) - 1 > 0`

If neither condition is met, the app reports the arc but shows no play — the signal is present but not yet strong enough to bet with positive expectation.

#### Storage

Spin log is persisted to `localStorage` under key `wheelBias.spinLog.v9`. Each new major schema version uses a new key to avoid loading stale data from a previous app version.
