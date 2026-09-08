# Sports Display Renderer

Renderer for the 7.3-inch reTerminal E1002 e-paper sports collectible display.

The repository contains independent MLB and NFL renderers. Both compose at high resolution and then downsample to the panel's native 480x800 portrait pixel grid.

## MLB — NL West

`render.py` composites the existing NL West pennant artwork onto the MLB master and overlays the current date plus live W-L / GB standings from MLB StatsAPI.

![NL West Standings](https://raw.githubusercontent.com/Dave356w/sports-display-renderer/main/public/mlb_nl_west.png)

```bash
pip install -r requirements.txt
python render.py
```

Output: `public/mlb_nl_west.png`

## NFL — NFC West

`render_nfl.py` draws the approved white-background NFC West collectible directly with Pillow, then overlays:

- current Pacific date
- live NFC West W-L record
- division record
- games behind the division leader
- the current week's unique NFC West matchups and Pacific kickoff times

Standings and schedule data come from ESPN's keyless NFL JSON feeds. The renderer keeps the MLB implementation's 971x1619 master -> 480x800 E1002 downsample path.

![NFC West Standings](https://raw.githubusercontent.com/Dave356w/sports-display-renderer/main/public/nfl_nfc_west.png)

```bash
pip install -r requirements.txt
python render_nfl.py
```

Deterministic 2026 Week 1 layout test:

```bash
NFL_SAMPLE=1 python render_nfl.py
```

Output: `public/nfl_nfc_west.png`

### NFC West page

`https://dave356w.github.io/sports-display-renderer/nfl.html`

### Automation

- `.github/workflows/render.yml` — MLB render every 6 hours.
- `.github/workflows/render_nfl.yml` — NFC West render every 30 minutes and on manual dispatch; it only commits when the generated PNG changes.

## Matchup site + grading ledger

The daily MLB matchup page, grading ledger, and vs-market scoreboard live in [mlb-matchup-site](https://github.com/Dave356w/mlb-matchup-site).

## License

The code in this repository is released under the [MIT License](LICENSE).

Team artwork is intended for personal, non-commercial use only. MLB and NFL team names, marks, and related trademarks belong to their respective clubs and leagues.
