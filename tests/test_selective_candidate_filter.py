"""Offline regression tests for high-confidence, high-value candidate selection."""
import unittest

from scripts.selective_candidate_filter import SelectionPolicy, select_candidates


def row(prob=0.85, lower=0.80, odds=1.5, samples=300):
    return {
        "race_id": "R001",
        "horse_id": "H001",
        "predicted_win_probability": prob,
        "conservative_win_probability": lower,
        "calibration_sample_count": samples,
        "prediction_time": "2026-10-25T13:00:00+09:00",
        "odds_snapshot": {
            "verified": True,
            "source": "licensed-fixture-for-unit-test",
            "observed_at": "2026-10-25T12:55:00+09:00",
            "decimal_odds": odds,
        },
    }


class SelectiveCandidateFilterTest(unittest.TestCase):
    def test_high_confidence_positive_expected_return(self):
        result = select_candidates([row()])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["prediction_count"], 1)
        self.assertIsNone(result["roi"])
        self.assertFalse(result["real_money_betting_enabled"])

    def test_high_probability_low_odds_skipped(self):
        result = select_candidates([row(odds=1.1)])
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("EXPECTED_RETURN_BELOW_THRESHOLD", result["all_predictions"][0]["reasons"])

    def test_longshot_skipped_even_if_positive_expected_return(self):
        result = select_candidates([row(prob=0.2, lower=0.15, odds=10)])
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("WIN_PROBABILITY_BELOW_THRESHOLD", result["all_predictions"][0]["reasons"])

    def test_missing_odds_skipped_and_no_return(self):
        r = row()
        r["odds_snapshot"] = None
        result = select_candidates([r])
        self.assertEqual(result["candidate_count"], 0)
        self.assertIsNone(result["all_predictions"][0]["expected_return_lower_bound"])

    def test_unverified_and_future_odds_skipped(self):
        for update in ({"verified": False}, {"observed_at": "2026-10-25T13:01:00+09:00"},
                       {"observed_at": "2026-10-25T12:55:00"}, {"source": ""}):
            with self.subTest(update=update):
                r = row()
                r["odds_snapshot"].update(update)
                self.assertEqual(select_candidates([r])["candidate_count"], 0)

    def test_insufficient_samples_and_overconfident_lower_bound(self):
        self.assertEqual(select_candidates([row(samples=3)])["candidate_count"], 0)
        self.assertEqual(select_candidates([row(prob=0.8, lower=0.9)])["candidate_count"], 0)

    def test_nan_inf_and_bad_odds_fail_closed(self):
        for p in (float("nan"), float("inf"), -0.1):
            with self.subTest(prob=p):
                self.assertEqual(select_candidates([row(prob=p)])["candidate_count"], 0)
        for odds in (float("nan"), float("inf"), 0.5):
            with self.subTest(odds=odds):
                self.assertEqual(select_candidates([row(odds=odds)])["candidate_count"], 0)

    def test_all_predictions_retained_even_when_none_qualify(self):
        result = select_candidates([row(odds=1.1), row(prob=0.2, lower=0.1, odds=10)])
        self.assertEqual(result["prediction_count"], 2)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(len(result["all_predictions"]), 2)

    def test_empty_day_is_valid(self):
        result = select_candidates([])
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["prediction_count"], 0)

    def test_policy_rejects_invalid_thresholds(self):
        with self.assertRaises(ValueError):
            SelectionPolicy(min_win_probability=1.5)
        with self.assertRaises(ValueError):
            SelectionPolicy(min_expected_return=float("nan"))


if __name__ == "__main__":
    unittest.main()
