"""Smoke tests for read-only market-gap database inventory."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.market_gap_data_audit import audit


class MarketGapAuditTests(unittest.TestCase):
    def test_counts_missing_values_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "example.db"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE races (race_id TEXT, weather TEXT, distance INTEGER)")
                con.execute("INSERT INTO races VALUES ('r1', 'rain', 1600)")
                con.execute("INSERT INTO races VALUES ('r2', NULL, 2000)")
            before = db.read_bytes()
            result = audit(str(db))
            self.assertEqual(result["tables"]["races"]["rows"], 2)
            self.assertEqual(result["tables"]["races"]["columns"]["weather"]["non_null"], 1)
            self.assertEqual(result["tables"]["races"]["columns"]["weather"]["null"], 1)
            self.assertEqual(result["feature_groups"]["track_weather"]["weather"][0]["table"], "races")
            self.assertEqual(result["provenance_status"], "NOT_VERIFIED")
            self.assertIsNone(result["roi"])
            self.assertEqual(db.read_bytes(), before)

    def test_missing_db_fails(self):
        with self.assertRaises(FileNotFoundError):
            audit("/nonexistent-keiba-db-for-test.db")


if __name__ == "__main__":
    unittest.main()
