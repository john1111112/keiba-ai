import unittest
from scripts.render_prerace_dashboard import render_dashboard


class DashboardTests(unittest.TestCase):
    def test_no_bet_and_escaping(self):
        report = {"race_id": "<script>alert(1)</script>",
                  "reason": "UNVERIFIED", "as_of_time": "2026-10-25T15:30:00+09:00",
                  "race_start_time": "2026-10-25T15:40:00+09:00",
                  "odds_drop_alerts": [{"horse_number": "<img src=x onerror=alert(1)>",
                                         "previous_odds": 10, "latest_odds": 5, "drop_pct": 50}]}
        output = render_dashboard(report)
        self.assertIn("NO BET", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertNotIn("<script>", output)
        self.assertIn("&lt;img", output)
        self.assertNotIn("<img", output)

    def test_missing_data_remains_no_bet(self):
        output = render_dashboard({"race_id": "r1", "status": "OK", "odds_drop_alerts": []})
        self.assertIn("NO BET", output)
        self.assertIn("有効な急落シグナルなし", output)


if __name__ == "__main__":
    unittest.main()
