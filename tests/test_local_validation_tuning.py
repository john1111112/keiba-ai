import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.local_validation_tuning import run


class LocalValidationTuningTests(unittest.TestCase):
    def test_local_sqlite_train_validation_only(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "tiny.db"
            with sqlite3.connect(db) as connection:
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
            before = db.read_bytes()
            result = run(
                str(db), train_start="2024-01-01", train_end="2024-12-31",
                validation_start="2025-01-01", validation_end="2025-12-31"
            )
            self.assertEqual(result["train_races"], 1)
            self.assertEqual(result["validation_races"], 1)
            self.assertEqual(result["holdout_status"], "NOT_ACCESSED")
            self.assertEqual(result["asof_status"], "NOT_CERTIFIED")
            self.assertIsNone(result["roi"])
            self.assertEqual(db.read_bytes(), before)

    def test_overlapping_periods_rejected(self):
        with self.assertRaises(ValueError):
            run("nonexistent.db", train_start="2025-01-01",
                train_end="2025-12-31", validation_start="2025-06-01",
                validation_end="2026-01-01")


if __name__ == "__main__":
    unittest.main()
