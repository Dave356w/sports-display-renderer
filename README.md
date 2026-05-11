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
