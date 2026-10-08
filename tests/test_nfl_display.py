"""Device-output gates: palette, bounds, and the schedule's widest cases."""
import unittest
from datetime import datetime

import render_nfl as nfl


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 10, 12, tzinfo=nfl.DISPLAY_TZ)
        self.games, self.week = nfl.sample_season()
        self.rows = nfl.compute_standings(self.games)

    def test_native_palette_and_schedule_variants(self):
        fourth = dict(self.games[0], home="ARI", away="WAS", home_score=100, away_score=100)
        games = self.games + [fourth]
        for style in ("illustrated", "text"):
            for count in range(5):
                with self.subTest(style=style, games=count):
                    img = nfl.render(self.rows, self.week, games[:count], self.now, style)
                    self.assertEqual(img.size, (480, 800))
                    self.assertEqual(img.mode, "RGB")
                    self.assertLessEqual(set(img.getdata()), set(nfl.PANEL_COLORS))
                    # The 12px physical-frame margin must remain blank.
                    self.assertEqual(set(img.crop((0, 0, 480, 12)).getdata()), {nfl.WHITE})

    def test_footer_title_is_crisp_monochrome_and_keeps_vintage_rules(self):
        img = nfl.render(self.rows, self.week, self.games, self.now, "text")
        crop = img.crop(nfl.TITLE_BOX)
        colors = set(crop.getdata())
        self.assertTrue(colors.issubset({nfl.BLACK, nfl.WHITE}))
        self.assertGreater(list(crop.getdata()).count(nfl.BLACK), 250)
        # The horizontal red accents must remain untouched beside the title.
        self.assertEqual(img.getpixel((100, 676)), nfl.RED)
        self.assertEqual(img.getpixel((380, 676)), nfl.RED)

    def test_late_season_records_fit_stat_columns(self):
        for row in self.rows:
            row.update(wl="10-6-1", div="3-2-1", gb="16.5")
        nfl.render(self.rows, 18, self.games, self.now, "text")
        for row in self.rows:
            for key, width in zip(("wl", "div", "gb"), nfl.STAT_WIDTHS):
                font = nfl.fitted_font(row[key], nfl.FONT_HEAVY, 28, width, 16)
                self.assertLessEqual(font.getlength(row[key]), width)
        for abbr in nfl.NFC_WEST:
            for style in ("illustrated", "text"):
                pennant = nfl.load_pennant(abbr, style)
                self.assertLess(nfl.PENNANT_POLE_X + pennant.width,
                                nfl.STAT_X[0] - nfl.STAT_WIDTHS[0] // 2)
                self.assertLessEqual(pennant.height, 91)

    def test_rams_text_pennant_lettering_is_upright_and_contained(self):
        pennant = nfl.load_pennant("LAR", "text")
        # Outer flag geometry is unchanged by the lettering redraw.
        self.assertEqual(pennant.size, (236, 81))
        panel = nfl.panel_artwork(pennant)
        self.assertLessEqual(set(panel.getdata()), set(nfl.PANEL_COLORS))
        # The piping is the largest yellow run; R, A, M and S must each be a
        # separate solid shape, none touching the border or broken apart.
        outline, *letters = _components(panel, nfl.YELLOW)
        self.assertEqual(len(letters), 4)
        self.assertGreater(len(outline), max(map(len, letters)))
        # Upright, not oblique: the R's stem keeps one left edge through the
        # middle of the letter (the italic art drifted ~8px over this span).
        r = min(letters, key=lambda shape: min(x for x, _ in shape))
        ys = [y for _, y in r]
        top, bottom = min(ys), max(ys)
        lefts = {min(x for x, y in r if y == row)
                 for row in range(top + (bottom - top) // 5, bottom - (bottom - top) // 5)}
        self.assertLessEqual(max(lefts) - min(lefts), 1)


def _components(img, color):
    """4-connected regions of one ink, largest first."""
    width, height = img.size
    px = img.load()
    seen, regions = set(), []
    for y in range(height):
        for x in range(width):
            if px[x, y] != color or (x, y) in seen:
                continue
            seen.add((x, y))
            stack, region = [(x, y)], []
            while stack:
                cx, cy = stack.pop()
                region.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if (0 <= nx < width and 0 <= ny < height and (nx, ny) not in seen
                            and px[nx, ny] == color):
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            regions.append(region)
    return sorted(regions, key=len, reverse=True)


if __name__ == "__main__":
    unittest.main()
