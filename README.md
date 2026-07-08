# mlb-matchup-site

Daily MLB matchup lean models with a graded W/L ledger and a vs-market
scoreboard, built and deployed by GitHub Actions.

- **Site:** https://dave356w.github.io/mlb-matchup-site/ (matchup cards)
- **Grades:** https://dave356w.github.io/mlb-matchup-site/grades.html
  (ledger + DK closing moneylines via ESPN, devigged `close_p_home`,
  vs-market z and flat ROI — the primary metrics, see `ANALYSES.md`)

Pipeline (3x daily, see `.github/workflows/build.yml`):
`build_site.py` builds the day's matchup models and `public/index.html`,
`grade_leans.py` ingests the lean dumps into `data/mlb_lean_ledger.csv`,
grades finished games, attaches closing lines (`market_backfill.py`), and
renders `public/grades.html`. See `MATCHUP_SITE.md` for details.

Split out of [sports-display-renderer](https://github.com/Dave356w/sports-display-renderer)
(the e-paper standings display), where the pre-split history lives.
