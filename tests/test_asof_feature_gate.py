import copy
import unittest

from scripts.asof_feature_gate import validate_asof_race


def runners():
    base = {
        "race_id": "r1",
        "prediction_at": "2025-01-01T10:00:00+09:00",
        "race_start_at": "2025-01-01T10:30:00+09:00",
        "features": {"age": 4.0, "weight_carry": 55.0},
        "feature_observed_at": {
            "age": "2024-12-31T00:00:00+09:00",
            "weight_carry": "2025-01-01T09:00:00+09:00",
        },
        "feature_source_ids": {"age": "licensed-record-1", "weight_carry": "licensed-record-2"},
    }
    return [dict(copy.deepcopy(base), horse_id="a"), dict(copy.deepcopy(base), horse_id="b")]


class AsofFeatureGateTests(unittest.TestCase):
    def test_structural_pass_does_not_certify_provenance(self):
        result = validate_asof_race(runners())
        self.assertEqual(result["status"], "STRUCTURAL_ASOF_PASS_PROVENANCE_UNVERIFIED")
        self.assertFalse(result["holdout_eligible"])

    def test_future_feature_rejected(self):
        rows = runners()
        rows[0]["feature_observed_at"]["age"] = "2025-01-01T10:01:00+09:00"
        with self.assertRaises(ValueError):
            validate_asof_race(rows)

    def test_postrace_feature_rejected(self):
        rows = runners()
        rows[0]["features"]["finish_order"] = 1
        rows[0]["feature_observed_at"]["finish_order"] = "2024-01-01T00:00:00Z"
        rows[0]["feature_source_ids"]["finish_order"] = "s"
        with self.assertRaises(ValueError):
            validate_asof_race(rows)

    def test_naive_timestamp_rejected(self):
        rows = runners()
        rows[0]["prediction_at"] = "2025-01-01T10:00:00"
        with self.assertRaises(ValueError):
            validate_asof_race(rows)

    def test_missing_timestamp_rejected(self):
        rows = runners()
        del rows[0]["feature_observed_at"]["age"]
        with self.assertRaises(ValueError):
            validate_asof_race(rows)

    def test_after_start_rejected(self):
        rows = runners()
        rows[0]["prediction_at"] = "2025-01-01T11:00:00+09:00"
        with self.assertRaises(ValueError):
            validate_asof_race(rows)

    def test_duplicate_runner_rejected(self):
        rows = runners()
        rows[1]["horse_id"] = "a"
        with self.assertRaises(ValueError):
            validate_asof_race(rows)


if __name__ == "__main__":
    unittest.main()
