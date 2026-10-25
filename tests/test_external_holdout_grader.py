import copy
import unittest

from scripts.external_holdout_grader import grade
from scripts.seal_holdout_predictions import seal


class ExternalGraderTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"race_id": "r1", "horse_id": "a", "predicted_win_probability": 0.8, "status": "RESEARCH_CANDIDATE"},
            {"race_id": "r1", "horse_id": "b", "predicted_win_probability": 0.2, "status": "SKIP"},
        ]
        self.outcomes = [
            {"race_id": "r1", "horse_id": "a", "win": 1},
            {"race_id": "r1", "horse_id": "b", "win": 0},
        ]
        self.sealed = seal(self.rows, git_commit="abc", model_sha256="def",
                           train_end="2023-12-31", validation_end="2024-12-31",
                           test_start="2025-01-01", test_end="2025-12-31", policy={})

    def test_correct_metrics(self):
        result = grade(self.sealed, self.outcomes)
        self.assertEqual(result["top1_accuracy"], 1)
        self.assertEqual(result["candidate_hit_rate"], 1)
        self.assertAlmostEqual(result["brier"], 0.04)
        self.assertIsNone(result["roi"])

    def test_tampered_prediction_rejected(self):
        changed = copy.deepcopy(self.sealed)
        changed["artifact"]["predictions"][0]["predicted_win_probability"] = 0.5
        with self.assertRaises(ValueError):
            grade(changed, self.outcomes)

    def test_missing_outcome_rejected(self):
        with self.assertRaises(ValueError):
            grade(self.sealed, self.outcomes[:1])

    def test_extra_outcome_rejected(self):
        with self.assertRaises(ValueError):
            grade(self.sealed, self.outcomes + [{"race_id": "r2", "horse_id": "c", "win": 1}])

    def test_probability_sum_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["predicted_win_probability"] = 0.7
        sealed = seal(rows, git_commit="abc", model_sha256="def",
                      train_end="2023-12-31", validation_end="2024-12-31",
                      test_start="2025-01-01", test_end="2025-12-31", policy={})
        with self.assertRaises(ValueError):
            grade(sealed, self.outcomes)

    def test_invalid_winner_count_rejected(self):
        bad = copy.deepcopy(self.outcomes)
        bad[1]["win"] = 1
        with self.assertRaises(ValueError):
            grade(self.sealed, bad)


if __name__ == "__main__":
    unittest.main()
