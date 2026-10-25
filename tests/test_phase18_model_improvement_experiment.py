import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.phase18_model_improvement_experiment import (
    compute_in_race_softmax,
    compute_in_race_implied_odds_probs,
    optimize_temperature,
    evaluate_model_predictions_on_races,
    run_phase18_experiment,
    main,
)


class TestPhase18ModelImprovementExperiment(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Create dummy deterministic single-race records
        self.dummy_race = [
            {"race_id": "R1", "horse_number": 1, "win": 1, "odds_value": 2.0, "raw_score": 1.5},
            {"race_id": "R1", "horse_number": 2, "win": 0, "odds_value": 4.0, "raw_score": 0.5},
            {"race_id": "R1", "horse_number": 3, "win": 0, "odds_value": 8.0, "raw_score": -0.5},
            {"race_id": "R1", "horse_number": 4, "win": 0, "odds_value": 16.0, "raw_score": -1.5},
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_in_race_softmax_properties(self):
        scores = [1.5, 0.5, -0.5, -1.5]
        probs = compute_in_race_softmax(scores, temperature=1.0)
        self.assertEqual(len(probs), 4)
        self.assertAlmostEqual(sum(probs), 1.0, places=6)
        self.assertTrue(probs[0] > probs[1] > probs[2] > probs[3])

        # High temperature approaches uniform
        probs_hot = compute_in_race_softmax(scores, temperature=100.0)
        for p in probs_hot:
            self.assertAlmostEqual(p, 0.25, places=2)

    def test_compute_in_race_implied_odds_probs(self):
        odds = [2.0, 4.0, 8.0, 16.0]
        probs = compute_in_race_implied_odds_probs(odds)
        self.assertEqual(len(probs), 4)
        self.assertAlmostEqual(sum(probs), 1.0, places=6)
        # Lowest odds should have highest implied win probability
        self.assertTrue(probs[0] > probs[1] > probs[2] > probs[3])

    def test_optimize_temperature(self):
        # Validation races
        val_races = [self.dummy_race, self.dummy_race]
        best_t = optimize_temperature(val_races, temp_candidates=[0.5, 1.0, 2.0])
        self.assertIn(best_t, [0.5, 1.0, 2.0])

    def test_evaluate_model_predictions_on_races(self):
        races_data = [self.dummy_race]
        for model_key in [
            "baseline_uniform",
            "baseline_implied_odds",
            "raw_uncalibrated",
            "candidate_in_race_softmax",
            "candidate_calibrated_in_race_softmax",
        ]:
            res = evaluate_model_predictions_on_races(races_data, model_key, temperature=1.0)
            self.assertIn("log_loss", res)
            self.assertIn("brier_score", res)
            self.assertIn("ece", res)
            self.assertIn("top1_accuracy", res)
            self.assertEqual(res["total_races"], 1)
            self.assertEqual(res["total_runners"], 4)

    def test_run_phase18_experiment_pipeline(self):
        report_file = self.tmp_path / "phase18_report.json"
        audit_file = self.tmp_path / "phase18_audit.jsonl"

        report = run_phase18_experiment(
            seed=42,
            n_races=24,
            n_folds=2,
            output_report=str(report_file),
            output_audit_log=str(audit_file),
        )

        self.assertTrue(report_file.exists())
        self.assertTrue(audit_file.exists())
        self.assertEqual(report["report_metadata"]["report_schema_version"], "18.0.0")
        self.assertEqual(report["report_metadata"]["random_seed"], 42)
        self.assertIn("dataset_fingerprint_sha256", report["report_metadata"])
        self.assertEqual(len(report["fold_results"]), 2)
        self.assertIn("model_selection_decision", report)
        self.assertIn(report["model_selection_decision"]["decision"], ["ACCEPTED", "REJECTED_NO_IMPROVEMENT"])
        self.assertEqual(report["roi_evaluation"]["roi_status"], "unevaluable_unverified_data")
        self.assertIsNone(report["roi_evaluation"]["roi"])
        self.assertTrue(report["audit_integrity"]["hash_chain_valid"])

    def test_run_phase18_experiment_rejects_insufficient_races(self):
        with self.assertRaises(ValueError):
            run_phase18_experiment(seed=42, n_races=10, n_folds=2)

    def test_cli_subcommands(self):
        report_file = self.tmp_path / "cli_report.json"
        audit_file = self.tmp_path / "cli_audit.jsonl"

        # 1. run command
        main([
            "run",
            "--seed", "42",
            "--races", "24",
            "--folds", "2",
            "--output-report", str(report_file),
            "--output-audit-log", str(audit_file),
        ])
        self.assertTrue(report_file.exists())

        # 2. summary command
        main(["summary", "--report", str(report_file)])


if __name__ == "__main__":
    unittest.main()
