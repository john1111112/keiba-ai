import unittest
from scripts.render_market_table import render_market_table


class MarketTableTests(unittest.TestCase):
    def test_renders_prices_and_changes(self):
        timeline = {"race_id": "r1", "observations": [
            {"observed_at": "2026-10-27T15:20:00+09:00",
             "horses": [{"horse_number": 1, "win_odds": 5.0,
                         "fetched_at": "2026-10-27T15:20:01+09:00"}]},
            {"observed_at": "2026-10-27T15:30:00+09:00",
             "horses": [{"horse_number": 1, "win_odds": 4.0,
                         "fetched_at": "2026-10-27T15:30:01+09:00"}]}]}
        movement = {"changes": [{"horse_number": 1, "change_percent": -20.0}]}
        result = render_market_table(timeline, movement)
        self.assertIn("4.0", result)
        self.assertIn("-20.0%", result)
        self.assertIn("25.0%", result)
        self.assertIn("市場暗示確率", result)
        self.assertIn("NO BET", result)

    def test_race_id_escaped(self):
        result = render_market_table({"race_id": "<script>alert(1)</script>",
                                      "observations": []})
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)

    def test_missing_data_does_not_create_odds(self):
        result = render_market_table({"race_id": "r", "observations": []})
        self.assertIn("取得済みオッズなし", result)


if __name__ == "__main__":
    unittest.main()
