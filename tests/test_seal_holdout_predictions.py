import copy
import unittest
from scripts.seal_holdout_predictions import seal, verify


class SealTests(unittest.TestCase):
    def make(self, rows=None):
        return seal(rows if rows is not None else [
            {"race_id": "r1", "horse_id": "h1", "predicted_win_probability": 0.6}
        ], git_commit="abc", model_sha256="def",
            train_end="2023-12-31", validation_end="2024-12-31",
            test_start="2025-01-01", test_end="2025-12-31",
            policy={"threshold": 0.75})

    def test_valid_seal_and_mutation_detection(self):
        record = self.make()
        self.assertTrue(verify(record))
        changed = copy.deepcopy(record)
        changed["artifact"]["predictions"][0]["predicted_win_probability"] = 0.9
        self.assertFalse(verify(changed))

    def test_outcome_is_forbidden(self):
        with self.assertRaises(ValueError):
            self.make([{"race_id": "r1", "horse_id": "h1", "win": 1}])

    def test_duplicate_is_forbidden(self):
        row = {"race_id": "r1", "horse_id": "h1"}
        with self.assertRaises(ValueError):
            self.make([row, row])

    def test_bad_split_is_forbidden(self):
        with self.assertRaises(ValueError):
            seal([{"race_id": "r1", "horse_id": "h1"}], git_commit="abc",
                 model_sha256="def", train_end="2025-01-01",
                 validation_end="2024-01-01", test_start="2025-02-01",
                 test_end="2025-12-31", policy={})


if __name__ == "__main__":
    unittest.main()
