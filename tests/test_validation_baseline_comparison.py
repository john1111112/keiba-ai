import unittest
from scripts.validation_baseline_comparison import compare_validation_baseline


class ValidationBaselineTests(unittest.TestCase):
    def test_metrics_and_explicit_no_holdout(self):
        races = [[
            {"horse_id": "a", "win": 1, "features": {"age": 0.0}},
            {"horse_id": "b", "win": 0, "features": {"age": 1.0}},
        ]]
        report = compare_validation_baseline(races, {"age": -1.0})
        self.assertEqual(report["status"], "IN_SAMPLE_VALIDATION_DIAGNOSTIC_ONLY")
        self.assertEqual(report["models"]["uniform"]["race_log_loss"], 0.6931471805599453)
        self.assertGreater(report["models"]["weighted"]["top1_accuracy"], 0)
        self.assertFalse(report["holdout_evaluated"])
        self.assertIsNone(report["roi"])

    def test_no_winner_rejected(self):
        races = [[
            {"horse_id": "a", "win": 0, "features": {"age": 0.0}},
            {"horse_id": "b", "win": 0, "features": {"age": 1.0}},
        ]]
        with self.assertRaises(ValueError):
            compare_validation_baseline(races, {"age": 1.0})


if __name__ == "__main__":
    unittest.main()
