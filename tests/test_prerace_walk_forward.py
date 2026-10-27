import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.walk_forward_prerace_research import walk_forward


class WalkForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / "bundles"
        self.folder.mkdir()
        labels = []
        for i in range(18):
            start = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
            bundle = {
                "race_id": f"race-{i}",
                "race_start_time": start.isoformat(),
                "captured_at": (start - timedelta(minutes=20)).isoformat(),
                "received_at": (start - timedelta(minutes=19)).isoformat(),
                "source": "manual_race_card",
                "features": [
                    {"horse_number": h, "bracket_number": h,
                     "age": 3 + (i % 4), "weight_carry": 54 + h,
                     "distance": 1200 + 200 * (i % 4),
                     "race_course": "東京" if i % 2 else "京都",
                     "surface": "芝", "condition": "良",
                     "sex": "牡" if h == 1 else "牝"}
                    for h in (1, 2)
                ],
            }
            (self.folder / f"{i:02d}.json").write_text(json.dumps(bundle), encoding="utf-8")
            for h in (1, 2):
                labels.append({"race_id": f"race-{i}", "horse_number": h,
                               "won": h == (1 if i % 2 else 2)})
        self.labels = self.root / "labels.json"
        self.labels.write_text(json.dumps(labels), encoding="utf-8")
        self.boundaries = [
            "2024-01-07T00:00:00+00:00",
            "2024-01-13T00:00:00+00:00",
            "2024-01-19T00:00:00+00:00",
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def test_expanding_training_and_disjoint_validation(self):
        report = walk_forward(self.folder, self.labels, boundaries=self.boundaries,
                              min_races=5)
        self.assertEqual(len(report["folds"]), 2)
        self.assertEqual([f["train_races"] for f in report["folds"]], [6, 12])
        self.assertEqual([f["validation_races"] for f in report["folds"]], [6, 6])
        self.assertEqual(report["total_validation_horses"], 24)
        self.assertEqual(report["bet_status"], "NO_BET")
        self.assertIn(report["research_quality_gate"],
                      ("BASELINE_NOT_BEATEN", "BASELINE_BEATEN_NOT_CERTIFIED"))
        self.assertLessEqual(report["folds_beating_uniform_winner_nll"], report["total_folds"])
        self.assertAlmostEqual(report["weighted_uniform_baseline_brier"], 0.25)
        self.assertAlmostEqual(report["weighted_uniform_race_winner_nll"], __import__("math").log(2))
        self.assertGreaterEqual(report["weighted_race_winner_nll_research_only"], 0)
        self.assertGreaterEqual(report["weighted_mean_brier"], 0.0)
        self.assertFalse(report["production_inference_allowed"])
        self.assertFalse(report["holdout_2026_accessed"])

    def test_nonchronological_boundaries_rejected(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            walk_forward(self.folder, self.labels, boundaries=self.boundaries[::-1],
                         min_races=5)

    def test_2026_boundary_rejected(self):
        with self.assertRaisesRegex(ValueError, "2026"):
            walk_forward(self.folder, self.labels,
                         boundaries=self.boundaries[:2] + ["2026-01-01T00:00:00+00:00"],
                         min_races=5)

    def test_missing_folds_rejected(self):
        with self.assertRaises(ValueError):
            walk_forward(self.folder, self.labels, boundaries=self.boundaries[:2],
                         min_races=5)


if __name__ == "__main__":
    unittest.main()
