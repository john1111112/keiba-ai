import json
import math
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.phase20_acceptance_readiness import (
    generate_readiness_matrix,
    run_safe_paper_execution_demo,
    execute_comprehensive_acceptance,
    ACCEPTANCE_SCHEMA_VERSION,
    main,
)
from scripts.phase19_realdata_validation import (
    audit_real_database_inventory,
    load_clean_real_races,
)
from scripts.phase15_walkforward_paper_monitor import verify_audit_log_hash_chain
from scripts.phase17_reproducible_paper_report import verify_integrated_audit_log_file


class TestPhase20AcceptanceReadiness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Build mock database with clean races
        self.mock_db = self.tmp_path / "mock_keiba.db"
        self._create_mock_database(self.mock_db, num_races=24)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_mock_database(self, db_path: Path, num_races: int = 24):
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE races (
                race_id TEXT PRIMARY KEY,
                race_name TEXT,
                date TEXT,
                race_course TEXT,
                race_number INTEGER,
                surface TEXT,
                distance INTEGER,
                direction TEXT,
                weather TEXT,
                condition TEXT,
                grade TEXT,
                created_at TEXT
            );
        """)
        c.execute("""
            CREATE TABLE race_results (
                id INTEGER PRIMARY KEY,
                race_id TEXT,
                finish_order INTEGER,
                bracket_number INTEGER,
                horse_number INTEGER,
                horse_id TEXT,
                horse_name TEXT,
                sex TEXT,
                age INTEGER,
                weight_carry REAL,
                jockey_id TEXT,
                jockey_name TEXT,
                time_str TEXT,
                time_sec REAL,
                margin_str TEXT,
                last_3f REAL,
                odds REAL,
                popularity INTEGER,
                horse_weight REAL,
                horse_weight_diff REAL,
                trainer_id TEXT,
                trainer_name TEXT,
                created_at TEXT,
                sire TEXT,
                sire_type TEXT,
                running_style TEXT,
                corner_4_pos INTEGER,
                rest_weeks INTEGER
            );
        """)
        c.execute("""
            CREATE TABLE payouts (
                id INTEGER PRIMARY KEY,
                race_id TEXT,
                bet_type TEXT,
                combination TEXT,
                payout INTEGER,
                popularity INTEGER
            );
        """)

        res_id = 1
        for r_idx in range(1, num_races + 1):
            race_id = f"20260101{r_idx:04d}"
            r_date = f"2026-01-{(r_idx % 25) + 1:02d}"
            c.execute(
                "INSERT INTO races VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (race_id, f"Race {r_idx}", r_date, "Tokyo", 11, "Turf", 2000, "R", "Fine", "Good", "G1", "2026-01-01 00:00:00")
            )
            for h_idx in range(1, 9):
                finish_order = h_idx
                c.execute(
                    """INSERT INTO race_results VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )""",
                    (
                        res_id, race_id, finish_order, (h_idx % 8) + 1, h_idx,
                        f"horse_{h_idx}", f"HorseName_{h_idx}", "Male", 4, 55.0,
                        "jockey_01", "JockeyName", "1:59.0", 119.0, "-", 34.0,
                        float(h_idx * 3.0), h_idx, 480.0, 0.0, "trainer_01", "TrainerName",
                        "2026-01-01 00:00:00", "Sire", "SireType", "Nige", 1, 4
                    )
                )
                res_id += 1

        conn.commit()
        conn.close()

    def test_generate_readiness_matrix_dimensions(self):
        inventory = audit_real_database_inventory(str(self.mock_db))
        dummy_metrics = {
            "baseline_uniform": {"log_loss": {"mean": 0.35}},
            "real_feature_calibrated_softmax": {"log_loss": {"mean": 0.32}},
        }
        code_ver = {"passed": True, "evidence": "All unit tests passed"}
        demo_res = {
            "hash_chain_valid": True,
            "demo_steps": [{"simulated_betting_action": "NO_BETS_PERMITTED_ZERO_COST", "roi": None}],
        }
        matrix = generate_readiness_matrix(
            real_metrics=dummy_metrics,
            inventory=inventory,
            code_verification=code_ver,
            demo_result=demo_res,
        )

        self.assertEqual(matrix["dimension_a_code_functionality"]["status"], "PASS")
        self.assertEqual(matrix["dimension_b_real_data_prediction_validity"]["status"], "PASS")
        self.assertEqual(matrix["dimension_c_pre_race_odds_authenticity"]["status"], "NOT_EVALUABLE")
        self.assertEqual(matrix["dimension_d_payout_authenticity"]["status"], "NOT_EVALUABLE")
        self.assertEqual(matrix["dimension_e_data_licensing_provenance"]["status"], "RESTRICTED")
        self.assertEqual(matrix["dimension_f_paper_only_operations"]["status"], "PASS")
        self.assertEqual(matrix["dimension_g_real_money_trading_readiness"]["status"], "PROHIBITED")

    def test_generate_readiness_matrix_dynamic_rejections(self):
        inventory = audit_real_database_inventory(str(self.mock_db))
        code_fail = {"passed": False, "error": "Unit test failure detected"}
        metrics_valid = {
            "baseline_uniform": {"log_loss": {"mean": 0.35}},
            "real_feature_calibrated_softmax": {"log_loss": {"mean": 0.32}},
        }
        demo_valid = {
            "hash_chain_valid": True,
            "demo_steps": [{"simulated_betting_action": "NO_BETS_PERMITTED_ZERO_COST", "roi": None}],
        }

        # 1. Code failure causes Dimension A to FAIL
        mat1 = generate_readiness_matrix(metrics_valid, inventory, code_verification=code_fail, demo_result=demo_valid)
        self.assertEqual(mat1["dimension_a_code_functionality"]["status"], "FAIL")

        # 2. Non-finite metric causes Dimension B to FAIL
        metrics_nan = {"baseline_uniform": {"log_loss": {"mean": float("nan")}}}
        mat2 = generate_readiness_matrix(metrics_nan, inventory, code_verification={"passed": True}, demo_result=demo_valid)
        self.assertEqual(mat2["dimension_b_real_data_prediction_validity"]["status"], "FAIL")

        # 3. Missing metrics payload causes Dimension B to be NOT_EVALUABLE
        mat3 = generate_readiness_matrix({}, inventory, code_verification={"passed": True}, demo_result=demo_valid)
        self.assertEqual(mat3["dimension_b_real_data_prediction_validity"]["status"], "NOT_EVALUABLE")

        # 4. Hash chain invalid in demo causes Dimension F to FAIL
        demo_chain_fail = {
            "hash_chain_valid": False,
            "demo_steps": [{"simulated_betting_action": "NO_BETS_PERMITTED_ZERO_COST", "roi": None}],
        }
        mat4 = generate_readiness_matrix(metrics_valid, inventory, code_verification={"passed": True}, demo_result=demo_chain_fail)
        self.assertEqual(mat4["dimension_f_paper_only_operations"]["status"], "FAIL")

    def test_run_safe_paper_execution_demo_validates_hash_chain(self):
        clean_races = load_clean_real_races(str(self.mock_db), limit_races=5)
        demo_res = run_safe_paper_execution_demo(clean_races)

        self.assertEqual(demo_res["demo_status"], "COMPLETED")
        self.assertEqual(demo_res["total_races_in_demo"], 5)
        self.assertTrue(demo_res["hash_chain_valid"])
        self.assertEqual(demo_res["audit_chain_length"], 5)

        # Verify audit chain with external verification function
        ver_res = verify_audit_log_hash_chain(demo_res["audit_chain"])
        self.assertTrue(ver_res["valid"])

    def test_run_safe_paper_execution_demo_tampering_detection(self):
        clean_races = load_clean_real_races(str(self.mock_db), limit_races=5)
        demo_res = run_safe_paper_execution_demo(clean_races)
        chain = demo_res["audit_chain"]

        # 1. Tamper with record payload
        tampered_chain = [dict(c) for c in chain]
        tampered_chain[1]["data"]["pipeline_action"] = "TAMPERED_ACTION"
        with self.assertRaises(ValueError):
            verify_audit_log_hash_chain(tampered_chain)

        # 2. Reorder records
        reordered_chain = [chain[1], chain[0]] + chain[2:]
        with self.assertRaises(ValueError):
            verify_audit_log_hash_chain(reordered_chain)

        # 3. Drop record (sequence break)
        dropped_chain = [chain[0], chain[2], chain[3], chain[4]]
        with self.assertRaises(ValueError):
            verify_audit_log_hash_chain(dropped_chain)

    def test_empty_and_anomalous_races_safe_handling(self):
        # 1. Empty races data input
        empty_res = run_safe_paper_execution_demo([])
        self.assertEqual(empty_res["demo_status"], "EMPTY_INPUT")
        self.assertFalse(empty_res["hash_chain_valid"])

        # 2. List containing empty race runners [[]]
        anomalous_res = run_safe_paper_execution_demo([[]])
        self.assertEqual(anomalous_res["races_skipped"], 1)
        self.assertEqual(anomalous_res["demo_steps"][0]["pipeline_action"], "SKIPPED_EMPTY_RACE")

    def test_execute_comprehensive_acceptance_with_audit_log_file(self):
        report_file = self.tmp_path / "acceptance_report.json"
        audit_file = self.tmp_path / "acceptance_audit.jsonl"

        rep = execute_comprehensive_acceptance(
            db_path=str(self.mock_db),
            output_report=str(report_file),
            output_audit_log=str(audit_file),
        )

        self.assertTrue(report_file.exists())
        self.assertTrue(audit_file.exists())
        self.assertEqual(rep["report_metadata"]["report_schema_version"], ACCEPTANCE_SCHEMA_VERSION)

        # Verify on-disk audit log verification result
        demo_info = rep["safe_paper_execution_demo"]
        self.assertTrue(demo_info["on_disk_audit_log_verified"])
        self.assertIsNone(demo_info["on_disk_audit_log_error"])

        # Read directly and verify
        ver_file = verify_integrated_audit_log_file(audit_file)
        self.assertTrue(ver_file["valid"])

    def test_cli_subcommands(self):
        report_file = self.tmp_path / "cli_acceptance.json"
        audit_file = self.tmp_path / "cli_audit.jsonl"

        # 1. check
        main([
            "check",
            "--db", str(self.mock_db),
            "--output-report", str(report_file),
            "--output-audit-log", str(audit_file),
        ])
        self.assertTrue(report_file.exists())
        self.assertTrue(audit_file.exists())

        # 2. summary
        main(["summary", "--report", str(report_file)])


if __name__ == "__main__":
    unittest.main()
