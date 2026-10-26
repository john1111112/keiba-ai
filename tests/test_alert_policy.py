import unittest
from src.alert_policy import evaluate_alert


class AlertPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = "2025-10-26T15:20:00+09:00"
        self.candidate = {
            "race_id": "20251026-tokyo-11", "horse_number": 3,
            "race_start_time": "2025-10-26T15:32:00+09:00",
            "odds_observed_at": "2025-10-26T15:19:20+09:00",
            "odds_fetched_at": "2025-10-26T15:19:25+09:00",
            "prediction_created_at": "2025-10-26T15:19:30+09:00",
            "win_odds": 6.2, "win_probability": 0.25,
            "odds_asof_certified": True, "model_asof_certified": True,
            "model_calibrated": True, "source": "jvlink_verified",
        }

    def evaluate(self, **changes):
        return evaluate_alert(self.candidate | changes, now=self.now)

    def test_eligible_manual_review(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "ALERT_ELIGIBLE")
        self.assertTrue(result["notification_allowed"])
        self.assertEqual(result["bet_status"], "REVIEW_ONLY")
        self.assertAlmostEqual(result["theoretical_ev"], 1.55)

    def test_uncertified_is_no_alert(self):
        for field in ("odds_asof_certified", "model_asof_certified", "model_calibrated"):
            with self.subTest(field=field):
                result = self.evaluate(**{field: False})
                self.assertEqual(result["status"], "NO_ALERT")
                self.assertEqual(result["bet_status"], "NO_BET")

    def test_stale_or_future_odds(self):
        for field, value in (
            ("odds_fetched_at", "2025-10-26T15:17:00+09:00"),
            ("odds_observed_at", "2025-10-26T15:21:00+09:00"),
            ("prediction_created_at", "2025-10-26T15:21:00+09:00"),
        ):
            with self.subTest(field=field):
                self.assertFalse(self.evaluate(**{field: value})["notification_allowed"])

    def test_unverified_source_rejected(self):
        self.assertEqual(self.evaluate(source="manual_screen")["status"], "NO_ALERT")

    def test_no_low_ev_spam(self):
        self.assertEqual(self.evaluate(win_odds=2.0)["status"], "NO_ALERT")

    def test_no_post_start_or_outside_window(self):
        self.assertEqual(self.evaluate(race_start_time="2025-10-26T15:19:00+09:00")["status"], "NO_ALERT")
        self.assertEqual(self.evaluate(race_start_time="2025-10-26T16:30:00+09:00")["status"], "NO_ALERT")

    def test_nan_and_naive_timestamps(self):
        self.assertEqual(self.evaluate(win_odds=float("nan"))["status"], "NO_ALERT")
        self.assertEqual(self.evaluate(odds_fetched_at="2025-10-26T15:19:25")["status"], "NO_ALERT")


if __name__ == "__main__":
    unittest.main()
