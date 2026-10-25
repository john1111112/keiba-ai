import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.phase17_reproducible_paper_report import (
    compute_dataset_fingerprint,
    check_finite_metrics,
    generate_reproducible_report,
    compare_reproducible_reports,
    verify_integrated_audit_log_file,
    main,
)


class TestPhase17ReproduciblePaperReport(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Create dummy deterministic race records
        self.records = [
            {"race_id": "202601010101", "horse_number": 1, "score": 0.8},
            {"race_id": "202601010101", "horse_number": 2, "score": 0.2},
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_dataset_fingerprint_deterministic(self):
        fp1 = compute_dataset_fingerprint(self.records)
        fp2 = compute_dataset_fingerprint(list(self.records))
        self.assertEqual(fp1, fp2)
        self.assertIsInstance(fp1, str)
        self.assertEqual(len(fp1), 64)

        # Mutated records should produce different fingerprint
        mutated = [dict(r) for r in self.records]
        mutated[0]["score"] = 0.85
        fp_mutated = compute_dataset_fingerprint(mutated)
        self.assertNotEqual(fp1, fp_mutated)

    def test_check_finite_metrics_validation(self):
        valid_metrics = {
            "log_loss": 0.45,
            "nested": {"brier": 0.12, "items": [0.1, 0.2]},
        }
        self.assertTrue(check_finite_metrics(valid_metrics))

        nan_metrics = {"log_loss": float("nan")}
        with self.assertRaises(ValueError):
            check_finite_metrics(nan_metrics)

        inf_metrics = {"log_loss": float("inf")}
        with self.assertRaises(ValueError):
            check_finite_metrics(inf_metrics)

    def test_generate_reproducible_report_and_hash_chain(self):
        report_file = self.tmp_path / "report_a.json"
        audit_file = self.tmp_path / "audit_a.jsonl"

        report = generate_reproducible_report(
            seed=42,
            n_races=24,
            n_folds=2,
            output_report=str(report_file),
            output_audit_log=str(audit_file),
        )

        self.assertTrue(report_file.exists())
        self.assertTrue(audit_file.exists())
        self.assertEqual(report["report_metadata"]["report_schema_version"], "17.0.0")
        self.assertEqual(report["report_metadata"]["random_seed"], 42)
        self.assertIn("dataset_fingerprint_sha256", report["report_metadata"])
        self.assertEqual(report["roi_evaluation"]["roi_status"], "unevaluable_unverified_data")
        self.assertIsNone(report["roi_evaluation"]["roi"])
        self.assertTrue(report["audit_integrity"]["hash_chain_valid"])

        # Verify audit file directly
        ver_res = verify_integrated_audit_log_file(audit_file)
        self.assertTrue(ver_res["valid"])
        self.assertGreaterEqual(ver_res["count"], 3)
        self.assertIsNone(ver_res.get("tampered_step"))

    def test_compare_reproducible_reports_identical_controls(self):
        report_a_file = self.tmp_path / "report_a.json"
        audit_a_file = self.tmp_path / "audit_a.jsonl"
        report_b_file = self.tmp_path / "report_b.json"
        audit_b_file = self.tmp_path / "audit_b.jsonl"

        rep_a = generate_reproducible_report(seed=42, n_races=24, n_folds=2, output_report=str(report_a_file), output_audit_log=str(audit_a_file))
        rep_b = generate_reproducible_report(seed=42, n_races=24, n_folds=2, output_report=str(report_b_file), output_audit_log=str(audit_b_file))

        comp = compare_reproducible_reports(rep_a, rep_b)
        self.assertTrue(comp["comparable"])
        self.assertEqual(comp["status"], "comparison_evaluated")
        self.assertIn("metric_deltas_b_minus_a", comp)
        self.assertIn("independent_sample_note", comp)

    def test_compare_reproducible_reports_rejects_divergent_controls(self):
        report_a_file = self.tmp_path / "report_a.json"
        audit_a_file = self.tmp_path / "audit_a.jsonl"
        report_diff_seed = self.tmp_path / "report_diff_seed.json"
        audit_diff_seed = self.tmp_path / "audit_diff_seed.jsonl"

        rep_a = generate_reproducible_report(seed=42, n_races=24, n_folds=2, output_report=str(report_a_file), output_audit_log=str(audit_a_file))
        # Differing seed and differing folds
        rep_diff = generate_reproducible_report(seed=999, n_races=24, n_folds=3, output_report=str(report_diff_seed), output_audit_log=str(audit_diff_seed))

        comp = compare_reproducible_reports(rep_a, rep_diff)
        self.assertFalse(comp["comparable"])
        self.assertEqual(comp["status"], "incomparable_experimental_controls")
        self.assertGreaterEqual(len(comp["discrepancies"]), 2)
        disc_text = " ".join(comp["discrepancies"])
        self.assertIn("seed", disc_text)
        self.assertIn("Split configuration", disc_text)

    def test_audit_log_tampering_detection(self):
        report_file = self.tmp_path / "report.json"
        audit_file = self.tmp_path / "audit.jsonl"

        generate_reproducible_report(seed=42, n_races=24, n_folds=2, output_report=str(report_file), output_audit_log=str(audit_file))

        # Read lines, tamper with middle line
        with open(audit_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        tampered_entry = json.loads(lines[1])
        tampered_entry["step"] = "TAMPERED_STEP"
        lines[1] = json.dumps(tampered_entry) + "\n"

        tampered_log = self.tmp_path / "tampered_audit.jsonl"
        with open(tampered_log, "w", encoding="utf-8") as f:
            f.writelines(lines)

        ver_res = verify_integrated_audit_log_file(tampered_log)
        self.assertFalse(ver_res["valid"])
        self.assertIsNotNone(ver_res.get("tampered_step"))

    def test_cli_subcommands(self):
        rep_cli_a = self.tmp_path / "cli_rep_a.json"
        aud_cli_a = self.tmp_path / "cli_aud_a.jsonl"
        rep_cli_b = self.tmp_path / "cli_rep_b.json"
        aud_cli_b = self.tmp_path / "cli_aud_b.jsonl"

        # 1. generate a
        main(["generate", "--seed", "42", "--races", "24", "--folds", "2", "--output-report", str(rep_cli_a), "--output-audit-log", str(aud_cli_a)])
        self.assertTrue(rep_cli_a.exists())

        # 2. generate b
        main(["generate", "--seed", "42", "--races", "24", "--folds", "2", "--output-report", str(rep_cli_b), "--output-audit-log", str(aud_cli_b)])
        self.assertTrue(rep_cli_b.exists())

        # 3. compare
        main(["compare", "--report-a", str(rep_cli_a), "--report-b", str(rep_cli_b)])

        # 4. verify-log
        main(["verify-log", "--audit-log", str(aud_cli_a)])


if __name__ == "__main__":
    unittest.main()
