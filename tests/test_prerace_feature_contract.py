import copy
import unittest

from scripts.validate_prerace_features import validate_feature_bundle


def sample():
    return {
        "race_id": "202610260811",
        "race_start_time": "2026-10-26T15:40:00+09:00",
        "captured_at": "2026-10-26T15:25:00+09:00",
        "received_at": "2026-10-26T15:25:02+09:00",
        "source": "manual_race_card",
        "features": [{
            "horse_number": 1, "bracket_number": 1, "age": 4,
            "weight_carry": 56.0, "distance": 1600,
            "race_course": "東京", "surface": "芝", "condition": "良", "sex": "牡"
        }],
    }


class PreraceFeatureContractTests(unittest.TestCase):
    cutoff = "2026-10-26T15:30:00+09:00"

    def test_valid_candidate_still_no_bet_and_no_inference(self):
        report = validate_feature_bundle(sample(), as_of_time=self.cutoff)
        self.assertEqual(report["horse_count"], 1)
        self.assertFalse(report["model_inference_allowed"])
        self.assertFalse(report["asof_certified"])
        self.assertEqual(report["bet_status"], "NO_BET")
        self.assertEqual(report["sha256"], validate_feature_bundle(sample(), as_of_time=self.cutoff)["sha256"])

    def test_forbidden_postrace_columns(self):
        for key in ("finish_order", "odds", "popularity", "target", "last_3f", "final_odds"):
            with self.subTest(key=key):
                payload = sample()
                payload["features"][0][key] = 1
                with self.assertRaises(ValueError):
                    validate_feature_bundle(payload, as_of_time=self.cutoff)

    def test_rejects_unknown_or_historical_features(self):
        payload = sample()
        payload["features"][0]["jockey_win_rate"] = 0.3
        with self.assertRaises(ValueError):
            validate_feature_bundle(payload, as_of_time=self.cutoff)

    def test_future_receipt_and_future_capture(self):
        for field in ("captured_at", "received_at"):
            payload = sample()
            payload[field] = "2026-10-26T15:35:00+09:00"
            with self.assertRaises(ValueError):
                validate_feature_bundle(payload, as_of_time=self.cutoff)

    def test_naive_time_and_poststart_cutoff(self):
        with self.assertRaises(ValueError):
            validate_feature_bundle(sample(), as_of_time="2026-10-26T15:30:00")
        with self.assertRaises(ValueError):
            validate_feature_bundle(sample(), as_of_time="2026-10-26T15:41:00+09:00")

    def test_duplicate_horse(self):
        payload = sample()
        payload["features"].append(copy.deepcopy(payload["features"][0]))
        with self.assertRaises(ValueError):
            validate_feature_bundle(payload, as_of_time=self.cutoff)

    def test_nan_and_bool_numeric_rejected(self):
        for field, value in (("weight_carry", float("nan")), ("distance", True), ("age", True)):
            payload = sample()
            payload["features"][0][field] = value
            with self.assertRaises(ValueError):
                validate_feature_bundle(payload, as_of_time=self.cutoff)

    def test_missing_source_and_unexpected_metadata(self):
        payload = sample()
        payload["source"] = "unknown"
        with self.assertRaises(ValueError):
            validate_feature_bundle(payload, as_of_time=self.cutoff)
        payload = sample()
        payload["result"] = "win"
        with self.assertRaises(ValueError):
            validate_feature_bundle(payload, as_of_time=self.cutoff)


if __name__ == "__main__":
    unittest.main()
