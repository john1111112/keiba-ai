import unittest
from src.alert_policy import evaluate_alert


class AlertFreshnessTests(unittest.TestCase):
    def candidate(self):
        return {
            "race_id": "race-1", "horse_number": 3,
            "race_start_time": "2026-10-27T13:20:00+09:00",
            "odds_observed_at": "2026-10-27T13:09:30+09:00",
            "odds_fetched_at": "2026-10-27T13:09:50+09:00",
            "win_odds": 5.0, "win_probability": 0.3,
            "odds_asof_certified": True, "model_asof_certified": True,
            "model_calibrated": True,
            "prediction_created_at": "2026-10-27T13:09:55+09:00",
            "source": "jvlink_verified",
        }

    def test_fresh_published_odds_can_be_eligible(self):
        result = evaluate_alert(self.candidate(), now="2026-10-27T13:10:00+09:00")
        self.assertTrue(result["notification_allowed"])
        self.assertEqual(result["odds_age_seconds"], 30)

    def test_recent_download_does_not_make_old_odds_fresh(self):
        row = self.candidate()
        row["odds_observed_at"] = "2026-10-27T13:00:00+09:00"
        result = evaluate_alert(row, now="2026-10-27T13:10:00+09:00")
        self.assertFalse(result["notification_allowed"])
        self.assertIn("Stale odds", result["reason"])

    def test_old_prediction_is_blocked(self):
        row = self.candidate()
        row["prediction_created_at"] = "2026-10-27T13:00:00+09:00"
        result = evaluate_alert(row, now="2026-10-27T13:10:00+09:00")
        self.assertFalse(result["notification_allowed"])
        self.assertIn("Stale model", result["reason"])


if __name__ == "__main__":
    unittest.main()
