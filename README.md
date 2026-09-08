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

`render_nfl.py` composites the NFC West pennant artwork in `assets/nfl/` onto the white-background NFC West master, then overlays:

- current Pacific date
- live NFC West W-L record
- division record
- games behind the division leader
- the current week's unique NFC West matchups and Pacific kickoff times

Schedule and result data come from [nflverse's `games.csv`](https://github.com/nflverse/nfldata), a keyless static file served off GitHub — no API key, quota, or user-agent gate. Standings are computed from completed regular-season games rather than read from a standings feed. The renderer keeps the MLB implementation's 971x1619 master -> 480x800 E1002 downsample path.

Clubs tied on winning percentage are ordered by a fixed NFC West fallback, not the NFL's full tiebreaker sequence.

Artwork lives in `assets/nfl/`: `background.png` (971x1619 master) plus one 2172x724 pennant per club (`SF.png`, `SEA.png`, `LAR.png`, `ARI.png`).

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
