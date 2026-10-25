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
        dummy_metrics = {"baseline_uniform": {"log_loss": {"mean": 0.35}}}
        matrix = generate_readiness_matrix(dummy_metrics, inventory)

        self.assertIn("dimension_a_code_functionality", matrix)
        self.assertIn("dimension_b_real_data_prediction_validity", matrix)
        self.assertIn("dimension_c_pre_race_odds_authenticity", matrix)
        self.assertIn("dimension_d_payout_authenticity", matrix)
        self.assertIn("dimension_e_data_licensing_provenance", matrix)
        self.assertIn("dimension_f_paper_only_operations", matrix)
        self.assertIn("dimension_g_real_money_trading_readiness", matrix)

        self.assertEqual(matrix["dimension_a_code_functionality"]["status"], "PASS")
        self.assertEqual(matrix["dimension_b_real_data_prediction_validity"]["status"], "PASS")
        self.assertEqual(matrix["dimension_c_pre_race_odds_authenticity"]["status"], "NOT_EVALUABLE")
        self.assertEqual(matrix["dimension_d_payout_authenticity"]["status"], "NOT_EVALUABLE")
        self.assertEqual(matrix["dimension_e_data_licensing_provenance"]["status"], "RESTRICTED")
        self.assertEqual(matrix["dimension_f_paper_only_operations"]["status"], "PASS")
        self.assertEqual(matrix["dimension_g_real_money_trading_readiness"]["status"], "PROHIBITED")

    def test_run_safe_paper_execution_demo(self):
        clean_races = load_clean_real_races(str(self.mock_db), limit_races=5)
        demo_res = run_safe_paper_execution_demo(clean_races)

        self.assertEqual(demo_res["demo_status"], "COMPLETED")
        self.assertEqual(demo_res["total_races_in_demo"], 5)
        self.assertTrue(demo_res["hash_chain_valid"])
        self.assertEqual(demo_res["audit_chain_length"], 5)

        for step in demo_res["demo_steps"]:
            self.assertEqual(step["simulated_betting_action"], "NO_BETS_PERMITTED_ZERO_COST")
            self.assertIsNone(step["roi"])

    def test_execute_comprehensive_acceptance_pipeline(self):
        report_file = self.tmp_path / "acceptance_report.json"
        rep = execute_comprehensive_acceptance(
            db_path=str(self.mock_db),
            output_report=str(report_file),
        )

        self.assertTrue(report_file.exists())
        self.assertEqual(rep["report_metadata"]["report_schema_version"], ACCEPTANCE_SCHEMA_VERSION)
        self.assertIn("readiness_matrix", rep)
        self.assertIn("final_readiness_determination", rep)

        det = rep["final_readiness_determination"]
        self.assertEqual(det["paper_research_status"], "READY_FOR_PAPER_RESEARCH")
        self.assertEqual(det["live_trading_status"], "BLOCKED_FOR_LIVE_TRADING")
        self.assertGreater(len(det["primary_blockers"]), 0)
        self.assertGreater(len(det["zero_cost_achievements"]), 0)

        # ROI strictly null
        self.assertEqual(rep["roi_evaluation"]["roi_status"], "unevaluable_unverified_data")
        self.assertIsNone(rep["roi_evaluation"]["roi"])

    def test_cli_subcommands(self):
        report_file = self.tmp_path / "cli_acceptance.json"

        # 1. check
        main(["check", "--db", str(self.mock_db), "--output-report", str(report_file)])
        self.assertTrue(report_file.exists())

        # 2. summary
        main(["summary", "--report", str(report_file)])


if __name__ == "__main__":
    unittest.main()
