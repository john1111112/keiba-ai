import unittest
from scripts.validation_weight_tuning import tune_weights


def race(rid, date, winning_first=True):
    return [
        {"race_id": rid, "race_date": date, "horse_id": rid + "a", "win": int(winning_first), "features": {"form": 1.0}},
        {"race_id": rid, "race_date": date, "horse_id": rid + "b", "win": int(not winning_first), "features": {"form": 0.0}},
    ]


class ValidationTuningTests(unittest.TestCase):
    def test_tuning_uses_validation_only(self):
        result = tune_weights([race("r1", "2023-01-01")],
                              [race("r2", "2024-01-01")], features=("form",))
        self.assertEqual(result["selection"]["weights"]["form"], 1.0)
        self.assertFalse(result["holdout_evaluated"])
        self.assertIsNone(result["roi"])

    def test_split_overlap_rejected(self):
        with self.assertRaises(ValueError):
            tune_weights([race("r1", "2023-01-01")],
                         [race("r1", "2024-01-01")], features=("form",))

    def test_future_training_rejected(self):
        with self.assertRaises(ValueError):
            tune_weights([race("r1", "2025-01-01")],
                         [race("r2", "2024-01-01")], features=("form",))

    def test_invalid_features_rejected(self):
        bad = race("r1", "2023-01-01")
        bad[0]["features"]["form"] = float("nan")
        with self.assertRaises(ValueError):
            tune_weights([bad], [race("r2", "2024-01-01")], features=("form",))

    def test_duplicate_winner_rejected(self):
        bad = race("r1", "2023-01-01")
        bad[1]["win"] = 1
        with self.assertRaises(ValueError):
            tune_weights([bad], [race("r2", "2024-01-01")], features=("form",))


if __name__ == "__main__":
    unittest.main()
