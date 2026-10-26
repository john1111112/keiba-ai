import json
import tempfile
import unittest
from pathlib import Path
from scripts.build_race_dashboard import build_dashboard


class RaceDashboardTests(unittest.TestCase):
    def test_real_snapshot_odds_shown_without_fake_ev(self):
        payload = {
            "received_at": "2026-10-27T12:00:00+09:00",
            "provenance_certified": False,
            "feed": {"published_at": "2026-10-27T11:59:00+09:00",
                     "races": [{"race_id": "R1", "race_time": "2026-10-27T13:00:00+09:00",
                                "race_status": "upcoming",
                                "horses": [{"horse_number": 1, "horse_name": "Example",
                                            "win_odds": 4.5,
                                            "odds_observed_at": "2026-10-27T11:58:00+09:00"}]}]}
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = build_dashboard(path, now="2026-10-27T12:01:00+09:00")
        self.assertEqual(result["runners"], 1)
        self.assertEqual(result["rows"][0]["win_odds"], 4.5)
        self.assertIsNone(result["rows"][0]["win_probability"])
        self.assertIsNone(result["rows"][0]["expected_value"])

    def test_supplied_win_probability_produces_ev_but_no_bet(self):
        payload = {"received_at": "2026-10-27T12:00:00+09:00",
                   "provenance_certified": False,
                   "feed": {"published_at": "2026-10-27T11:59:00+09:00",
                            "races": [{"race_id": "R1", "race_time": "2026-10-27T13:00:00+09:00",
                                       "race_status": "upcoming",
                                       "horses": [{"horse_number": 1, "horse_name": "Example",
                                                   "win_odds": 4.5,
                                                   "odds_observed_at": "2026-10-27T11:58:00+09:00"}]}]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = build_dashboard(path, now="2026-10-27T12:01:00+09:00",
                                     predictions={("R1", 1): 0.3})
        self.assertEqual(result["rows"][0]["expected_value"], 1.35)
        self.assertEqual(result["bet_status"], "NO_BET")

    def test_rejects_unverified_future_publication(self):
        payload = {"received_at": "2026-10-27T12:00:00+09:00",
                   "provenance_certified": False,
                   "feed": {"published_at": "2026-10-27T12:01:00+09:00", "races": []}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                build_dashboard(path, now="2026-10-27T12:02:00+09:00")


if __name__ == "__main__":
    unittest.main()
