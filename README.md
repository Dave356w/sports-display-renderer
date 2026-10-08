# Sports Display Renderer

Renderer for the 7.3-inch reTerminal E1002 e-paper sports collectible display.

The repository contains independent MLB and NFL renderers. Both output the panel's native 480x800 portrait pixel grid. The NFL renderer draws text and rules directly at device resolution; the MLB renderer retains its original master/downsample pipeline.

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
- the current week's unique NFC West matchups, each showing its Pacific kickoff time until the game is final and its score afterwards

Schedule and result data come from [nflverse's `games.csv`](https://github.com/nflverse/nfldata), a keyless static file served off GitHub — no API key, quota, or user-agent gate. Standings are computed from completed regular-season games rather than read from a standings feed. The NFL renderer scales the artwork first, then draws solid black text and pixel-aligned rules at the final 480x800 resolution. The original This Week section retains its artwork, single-row game columns, font sizes, colors, and live schedule/result behavior.

Clubs tied on winning percentage are ordered by a fixed NFC West fallback, not the NFL's full tiebreaker sequence.

Artwork lives in `assets/nfl/`: `background.png` (971x1619 master) plus one 2172x724 illustrated pennant per club (`SF.png`, `SEA.png`, `LAR.png`, `ARI.png`). The original masthead and illustrated pennants are retained. `text-only-pennants.png` is a separate generated concept sheet with oversized team lettering and no small illustrations; the renderer crops its four regions at runtime.

### E1002 artwork treatment

- Exact 480x800 portrait RGB PNG; standings text is drawn at native resolution without dithering. The weekly-games footer keeps its original rendering.
- Updated artwork uses six nominal encoding colors: black, white, red, green, blue, yellow. These are not measured physical ink colors. Dark team blues/reds are mapped by hue so a nearest-RGB conversion does not erase the pennants into black.
- White background, solid black numbers, 1–2px rules and a 12px outer margin.
- Long/tied standings records fit their own column without overlapping. The This Week layout is unchanged.
- Two outputs share one data snapshot: `public/nfl_nfc_west.png` (illustrated) and `public/nfl_nfc_west_text.png` (text-only exploration). The existing page keeps the illustrated version as its default.

Display the PNG at 1:1 resolution. In the device/content software, avoid additional smoothing, resizing, or dithering where configurable. If firmware expects a landscape 800x480 buffer, rotate the portrait image by 90 degrees without resampling rather than stretching it. Actual contrast and colors still need a check on the physical screen.

Hardware reference: [Seeed E1002 specifications](https://wiki.seeedstudio.com/getting_started_with_reterminal_e1002/).

![NFC West Standings](https://raw.githubusercontent.com/Dave356w/sports-display-renderer/main/public/nfl_nfc_west.png)

```bash
pip install -r requirements.txt
python render_nfl.py
```

Deterministic 2026 Week 1 layout sample (fixed date, not live results):

```bash
NFL_SAMPLE=1 python render_nfl.py
```

Outputs: `public/nfl_nfc_west.png` and `public/nfl_nfc_west_text.png`

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
