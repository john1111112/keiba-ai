import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.train_prerace_research import train_research_model


def make_bundle(i, start_year=2024):
    start = datetime(start_year, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
    return {
        "race_id": f"race-{i}",
        "race_start_time": start.isoformat(),
        "captured_at": (start - timedelta(minutes=20)).isoformat(),
        "received_at": (start - timedelta(minutes=19)).isoformat(),
        "source": "manual_race_card",
        "features": [
            {"horse_number": h, "bracket_number": h, "age": 3 + (i % 4),
             "weight_carry": 54 + h, "distance": 1200 + 200 * (i % 4),
             "race_course": "東京" if i % 2 else "京都", "surface": "芝",
             "condition": "良", "sex": "牡" if h == 1 else "牝"}
            for h in (1, 2)
        ],
    }


class ResearchModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundles = self.root / "bundles"
        self.bundles.mkdir()
        labels = []
        for i in range(12):
            bundle = make_bundle(i)
            (self.bundles / f"{i:02d}.json").write_text(json.dumps(bundle), encoding="utf-8")
            for h in (1, 2):
                labels.append({"race_id": f"race-{i}", "horse_number": h,
                               "won": h == (1 if i % 2 else 2)})
        self.labels = self.root / "labels.json"
        self.labels.write_text(json.dumps(labels), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_model(self):
        return train_research_model(
            self.bundles, self.labels,
            train_before="2024-01-07T00:00:00+00:00",
            validate_before="2024-01-13T00:00:00+00:00",
            min_races=5)

    def test_chronological_split_and_no_bets(self):
        result = self.run_model()
        self.assertEqual(result["train_races"], 6)
        self.assertEqual(result["validation_races"], 6)
        self.assertEqual(result["train_horses"], 12)
        self.assertEqual(result["validation_horses"], 12)
        self.assertFalse(result["production_inference_allowed"])
        self.assertFalse(result["asof_certified"])
        self.assertFalse(result["holdout_2026_accessed"])
        self.assertEqual(result["bet_status"], "NO_BET")
        self.assertNotIn("finish_order", result["features"])
        self.assertNotIn("odds", result["features"])
        self.assertGreaterEqual(result["brier_score"], 0)
        self.assertGreaterEqual(result["race_normalized_brier_research_only"], 0)
        self.assertAlmostEqual(result["uniform_race_winner_nll"], __import__("math").log(2))
        self.assertGreaterEqual(result["race_winner_nll_research_only"], 0)
        self.assertLessEqual(result["brier_score"], 1)

    def test_sealed_2026_bundle_rejected(self):
        (self.bundles / "sealed.json").write_text(json.dumps(make_bundle(90, 2026)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sealed"):
            self.run_model()

    def test_missing_label_rejected(self):
        labels = json.loads(self.labels.read_text(encoding="utf-8"))
        labels.pop()
        self.labels.write_text(json.dumps(labels), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Missing result label"):
            self.run_model()

    def test_leaked_feature_rejected(self):
        path = self.bundles / "00.json"
        bundle = json.loads(path.read_text(encoding="utf-8"))
        bundle["features"][0]["finish_order"] = 1
        path.write_text(json.dumps(bundle), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_model()

    def test_duplicate_label_rejected(self):
        labels = json.loads(self.labels.read_text(encoding="utf-8"))
        labels.append(labels[0])
        self.labels.write_text(json.dumps(labels), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate label"):
            self.run_model()

    def test_future_validation_window_rejected(self):
        with self.assertRaises(ValueError):
            train_research_model(self.bundles, self.labels,
                                 train_before="2024-01-07T00:00:00+00:00",
                                 validate_before="2026-01-01T00:00:00+00:00")

    def test_insufficient_races_rejected(self):
        with self.assertRaisesRegex(ValueError, "Insufficient"):
            train_research_model(self.bundles, self.labels,
                                 train_before="2024-01-07T00:00:00+00:00",
                                 validate_before="2024-01-13T00:00:00+00:00",
                                 min_races=20)


if __name__ == "__main__":
    unittest.main()
