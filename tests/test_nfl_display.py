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

    def test_all_kickoff_labels_fit_at_readable_size(self):
        for day in range(7):
            for hour in range(24):
                dt = self.now.replace(day=day + 1, hour=hour, minute=59)
                label = nfl.time_label(dt)
                font = nfl.fitted_font(label, nfl.FONT_BOLD, 16, 192, 15)
                self.assertGreaterEqual(font.size, 15)
                self.assertLessEqual(font.getlength(label), 192)


if __name__ == "__main__":
    unittest.main()
