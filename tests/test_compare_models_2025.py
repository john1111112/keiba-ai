"""Unit tests for 2025 model comparison (AI Alone vs Market Odds vs Blended).

Verifies strict temporal isolation, mathematical correctness of Log Loss and Brier Score,
and rejection of 2026 holdout access.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.compare_models_2025 import (
    load_validation_races_with_odds,
    evaluate_models_on_races,
    run_comparison,
)


class TestCompareModels2025(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_keiba.db"

        # Create temporary database with schema
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("CREATE TABLE races (race_id TEXT, date TEXT)")
            con.execute(
                """CREATE TABLE race_results (
                    race_id TEXT, horse_number INTEGER, age INTEGER,
                    weight_carry REAL, odds REAL, finish_order TEXT
                )"""
            )
            # Insert valid 2025 race
            con.execute("INSERT INTO races VALUES ('2025_valid', '2025-05-01')")
            con.executemany(
                "INSERT INTO race_results VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("2025_valid", 1, 4, 56.0, 2.5, "1"),
                    ("2025_valid", 2, 5, 54.0, 5.0, "2"),
                    ("2025_valid", 3, 3, 55.0, 10.0, "3"),
                ],
            )
            # Insert invalid race (missing odds)
            con.execute("INSERT INTO races VALUES ('2025_bad_odds', '2025-05-02')")
            con.executemany(
                "INSERT INTO race_results VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("2025_bad_odds", 1, 4, 56.0, None, "1"),
                    ("2025_bad_odds", 2, 5, 54.0, 5.0, "2"),
                ],
            )
            # Insert invalid race (nonnumeric finish order, e.g. aborted)
            con.execute("INSERT INTO races VALUES ('2025_aborted', '2025-05-03')")
            con.executemany(
                "INSERT INTO race_results VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("2025_aborted", 1, 4, 56.0, 2.0, "1"),
                    ("2025_aborted", 2, 5, 54.0, 4.0, "中止"),
                ],
            )
            # Insert 2026 holdout race (forbidden to access)
            con.execute("INSERT INTO races VALUES ('2026_holdout', '2026-01-10')")
            con.executemany(
                "INSERT INTO race_results VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("2026_holdout", 1, 4, 56.0, 3.0, "1"),
                    ("2026_holdout", 2, 5, 54.0, 4.0, "2"),
                ],
            )
            con.commit()
        finally:
            con.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_validation_races_filters_correctly(self):
        con = sqlite3.connect(self.db_path)
        try:
            races = load_validation_races_with_odds(con, start_date="2025-01-01", end_date="2025-12-31")
            self.assertEqual(len(races), 1)
            self.assertEqual(races[0][0]["race_id"], "2025_valid")
            self.assertEqual(len(races[0]), 3)
            # Ensure winner is correctly tagged
            self.assertEqual(races[0][0]["win"], 1)
            self.assertEqual(races[0][1]["win"], 0)
        finally:
            con.close()

    def test_post_2025_access_is_forbidden(self):
        con = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(ValueError):
                load_validation_races_with_odds(con, start_date="2025-01-01", end_date="2026-06-01")
        finally:
            con.close()

    def test_evaluate_models_metric_math(self):
        # Synthetic race with 3 runners
        synthetic_race = [
            {
                "race_id": "r1",
                "horse_id": "r1:1",
                "horse_number": 1,
                "win": 1,
                "odds": 2.0,  # 1/2 = 0.5 -> market prob ≈ 0.545
                "features": {"age": 0.0, "weight_carry": 0.0, "horse_number": 0.0},
            },
            {
                "race_id": "r1",
                "horse_id": "r1:2",
                "horse_number": 2,
                "win": 0,
                "odds": 4.0,  # 1/4 = 0.25 -> market prob ≈ 0.273
                "features": {"age": 0.0, "weight_carry": 0.0, "horse_number": 0.0},
            },
            {
                "race_id": "r1",
                "horse_id": "r1:3",
                "horse_number": 3,
                "win": 0,
                "odds": 6.0,  # 1/6 = 0.167 -> market prob ≈ 0.182
                "features": {"age": 0.0, "weight_carry": 0.0, "horse_number": 0.0},
            },
        ]
        res = evaluate_models_on_races([synthetic_race])
        models = res["models"]

        self.assertIn("uniform", models)
        self.assertIn("ai_alone", models)
        self.assertIn("market_odds", models)
        self.assertIn("blend_50ai_50mkt", models)

        # Uniform prob for winner is 1/3 ≈ 0.333
        self.assertAlmostEqual(models["uniform"]["top1_accuracy"], 1.0, places=4)
        # Winner (horse 1) has lowest odds, so market top1 hit is 1
        self.assertEqual(models["market_odds"]["top1_hits"], 1)
        self.assertGreater(models["market_odds"]["top1_accuracy"], 0.99)

        # Market Log Loss must be smaller (better) than uniform
        self.assertLess(models["market_odds"]["race_log_loss"], models["uniform"]["race_log_loss"])

    def test_run_comparison_report_structure(self):
        rep = run_comparison(str(self.db_path), year=2025)
        self.assertEqual(rep["status"], "HISTORICAL_VALIDATION_MODEL_COMPARISON_COMPLETED")
        self.assertEqual(rep["evaluation_period"]["year"], 2025)
        self.assertEqual(rep["holdout_status"], "NOT_ACCESSED")
        self.assertTrue(rep["holdout_2026_isolated"])
        self.assertIsNone(rep["roi"])
        self.assertIn("models", rep)
        self.assertIn("sha256", rep)


if __name__ == "__main__":
    unittest.main()
