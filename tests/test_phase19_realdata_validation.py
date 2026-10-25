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

from scripts.phase19_realdata_validation import (
    audit_real_database_inventory,
    load_clean_real_races,
    evaluate_models_on_real_races,
    run_phase19_real_validation,
    PRE_RACE_ELIGIBLE_COLS,
    POST_RACE_LEAKAGE_COLS,
    main,
)


class TestPhase19RealDataValidation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Build a temporary SQLite database mimicking keiba.db
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

        # Insert mock races
        res_id = 1
        for r_idx in range(1, num_races + 1):
            race_id = f"20260101{r_idx:04d}"
            r_date = f"2026-01-{(r_idx % 25) + 1:02d}"
            c.execute(
                "INSERT INTO races VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (race_id, f"Race {r_idx}", r_date, "Tokyo", 11, "Turf", 2000, "R", "Fine", "Good", "G1", "2026-01-01 00:00:00")
            )
            # 8 runners per race, exactly 1 winner
            for h_idx in range(1, 9):
                finish_order = h_idx  # runner 1 wins
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

    def test_pre_race_and_post_race_classification_integrity(self):
        # Verify no overlap between pre-race eligible and post-race leakage
        pre_keys = set(PRE_RACE_ELIGIBLE_COLS.keys())
        post_keys = set(POST_RACE_LEAKAGE_COLS.keys())
        self.assertEqual(len(pre_keys.intersection(post_keys)), 0)
        self.assertIn("finish_order", post_keys)
        self.assertIn("time_sec", post_keys)
        self.assertIn("odds", post_keys)
        self.assertIn("weight_carry", pre_keys)
        self.assertIn("bracket_number", pre_keys)

    def test_audit_real_database_inventory_on_mock(self):
        inv = audit_real_database_inventory(str(self.mock_db))
        self.assertEqual(inv["historical_scope"]["total_races"], 24)
        self.assertEqual(inv["historical_scope"]["total_runners"], 24 * 8)
        self.assertEqual(inv["historical_scope"]["races_with_clean_single_winner"], 24)
        self.assertIn("races", inv["tables"])
        self.assertIn("race_results", inv["tables"])
        self.assertFalse(inv["data_license_and_provenance"]["licensed_api_connection"])

    def test_audit_real_database_inventory_on_production_keiba_db(self):
        # Inspect real production keiba.db read-only if present
        prod_db = PROJECT_ROOT / "data" / "keiba.db"
        if prod_db.exists():
            inv = audit_real_database_inventory(str(prod_db))
            self.assertEqual(inv["historical_scope"]["total_races"], 3174)
            self.assertEqual(inv["historical_scope"]["total_runners"], 43358)
            self.assertGreater(inv["historical_scope"]["races_with_clean_single_winner"], 3000)

    def test_load_clean_real_races(self):
        races = load_clean_real_races(str(self.mock_db))
        self.assertEqual(len(races), 24)
        for r in races:
            self.assertEqual(len(r), 8)
            # Check exactly 1 winner
            wins = [runner["win"] for runner in r]
            self.assertEqual(sum(wins), 1)
            # Ensure raw_score is finite
            for runner in r:
                self.assertFalse(math.isnan(runner["raw_score"]))
                self.assertFalse(math.isinf(runner["raw_score"]))

    def test_evaluate_models_on_real_races(self):
        races = load_clean_real_races(str(self.mock_db), limit_races=5)
        for m_type in ["baseline_uniform", "real_feature_softmax", "real_feature_calibrated_softmax"]:
            res = evaluate_models_on_real_races(races, m_type, temperature=1.0)
            self.assertIn("log_loss", res)
            self.assertIn("brier_score", res)
            self.assertIn("ece", res)
            self.assertIn("top1_accuracy", res)
            self.assertEqual(res["total_races"], 5)

    def test_run_phase19_real_validation_pipeline(self):
        report_file = self.tmp_path / "report.json"
        audit_file = self.tmp_path / "audit.jsonl"

        report = run_phase19_real_validation(
            db_path=str(self.mock_db),
            n_races_eval=24,
            n_folds=2,
            seed=42,
            output_report=str(report_file),
            output_audit_log=str(audit_file),
        )

        self.assertTrue(report_file.exists())
        self.assertTrue(audit_file.exists())
        self.assertEqual(report["report_metadata"]["report_schema_version"], "19.0.0")
        self.assertIn("database_inventory", report)
        self.assertIn("data_deficiencies_for_phase20", report)
        self.assertEqual(report["roi_evaluation"]["roi_status"], "unevaluable_unverified_data")
        self.assertIsNone(report["roi_evaluation"]["roi"])
        self.assertTrue(report["audit_integrity"]["hash_chain_valid"])

    def test_run_phase19_rejects_insufficient_races(self):
        # Empty mock DB
        tiny_db = self.tmp_path / "tiny.db"
        self._create_mock_database(tiny_db, num_races=5)
        rep = run_phase19_real_validation(db_path=str(tiny_db), n_races_eval=5, n_folds=2)
        self.assertEqual(rep["status"], "NOT_EVALUABLE")

    def test_cli_subcommands(self):
        rep_file = self.tmp_path / "cli_rep.json"
        aud_file = self.tmp_path / "cli_aud.jsonl"

        # 1. inventory
        main(["inventory", "--db", str(self.mock_db)])

        # 2. evaluate
        main([
            "evaluate",
            "--db", str(self.mock_db),
            "--races", "24",
            "--folds", "2",
            "--output-report", str(rep_file),
            "--output-audit-log", str(aud_file),
        ])
        self.assertTrue(rep_file.exists())


if __name__ == "__main__":
    unittest.main()
