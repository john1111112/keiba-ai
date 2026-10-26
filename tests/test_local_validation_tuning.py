import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.local_validation_tuning import run


class LocalValidationTuningTests(unittest.TestCase):
    def test_local_sqlite_train_validation_only(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "tiny.db"
            connection = sqlite3.connect(db)
            try:
                connection.execute("CREATE TABLE races (race_id TEXT, date TEXT)")
                connection.execute(
                    "CREATE TABLE race_results (race_id TEXT, horse_number INTEGER, age INTEGER, weight_carry REAL, finish_order INTEGER)"
                )
                for rid, date in [
                    ("train", "2024-10-01"),
                    ("val", "2025-06-01"),
                    ("holdout", "2026-06-01"),
                ]:
                    connection.execute("INSERT INTO races VALUES (?, ?)", (rid, date))
                    connection.executemany(
                        "INSERT INTO race_results VALUES (?, ?, ?, ?, ?)",
                        [(rid, 1, 4, 55, 1), (rid, 2, 5, 56, 2)],
                    )
                connection.commit()
            finally:
                connection.close()

            before = db.read_bytes()
            result = run(
                str(db), train_start="2024-01-01", train_end="2024-12-31",
                validation_start="2025-01-01", validation_end="2025-12-31"
            )
            self.assertEqual(result["train_races"], 1)
            self.assertEqual(result["validation_races"], 1)
            self.assertEqual(result["holdout_status"], "NOT_ACCESSED")
            comparison = result["validation_baseline_comparison"]
            self.assertEqual(comparison["status"], "IN_SAMPLE_VALIDATION_DIAGNOSTIC_ONLY")
            self.assertEqual(comparison["race_count"], 1)
            self.assertIn("weighted", comparison["models"])
            self.assertIn("uniform", comparison["models"])
            self.assertEqual(result["asof_status"], "NOT_CERTIFIED")
            self.assertIsNone(result["roi"])
            self.assertEqual(db.read_bytes(), before)

    def test_text_finish_order_and_incomplete_race(self):
        from scripts.local_validation_tuning import load_period
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "text_finish.db"
            connection = sqlite3.connect(db)
            try:
                connection.execute("CREATE TABLE races (race_id TEXT, date TEXT)")
                connection.execute(
                    "CREATE TABLE race_results (race_id TEXT, horse_number INTEGER, age INTEGER, weight_carry REAL, finish_order TEXT)"
                )
                connection.executemany("INSERT INTO races VALUES (?, ?)", [
                    ("valid", "2025-06-01"),
                    ("incomplete", "2025-06-02"),
                    ("nonnumeric", "2025-06-03"),
                ])
                connection.executemany("INSERT INTO race_results VALUES (?, ?, ?, ?, ?)", [
                    ("valid", 1, 4, 55, "1"),
                    ("valid", 2, 5, 56, "2"),
                    ("incomplete", 1, 4, 55, "1"),
                    ("incomplete", 2, None, 56, "2"),
                    ("nonnumeric", 1, 4, 55, "1"),
                    ("nonnumeric", 2, 5, 56, "中止"),
                ])
                connection.commit()
                races = load_period(connection, start="2025-01-01", end="2025-12-31")
                self.assertEqual(len(races), 1)
                self.assertEqual(races[0][0]["race_id"], "valid")
                self.assertEqual(races[0][0]["win"], 1)
            finally:
                connection.close()

    def test_overlapping_periods_rejected(self):
        with self.assertRaises(ValueError):
            run("nonexistent.db", train_start="2025-01-01",
                train_end="2025-12-31", validation_start="2025-06-01",
                validation_end="2026-01-01")


if __name__ == "__main__":
    unittest.main()
