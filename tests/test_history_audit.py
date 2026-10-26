import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.audit_prerace_history import audit_odds_history


class HistoryAuditTests(unittest.TestCase):
    def test_missing_db_no_bet(self):
        with tempfile.TemporaryDirectory() as directory:
            result = audit_odds_history(Path(directory) / "missing.db")
            self.assertEqual(result["status"], "NO_ODDS_DB")
            self.assertEqual(result["bet_status"], "NO_BET")
            self.assertFalse(result["asof_certified"])

    def test_counts_and_excludes_bad_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "odds.db"
            with sqlite3.connect(db) as conn:
                conn.execute("""CREATE TABLE odds_timeline (
                    id INTEGER PRIMARY KEY, race_id TEXT, horse_number INTEGER,
                    odds_type TEXT, odds_value REAL, observed_at TEXT,
                    fetched_at TEXT, source TEXT, data_status TEXT)""")
                def add(horse, odds, observed, fetched, status="manual_prerace"):
                    conn.execute("""INSERT INTO odds_timeline
                        (race_id, horse_number, odds_type, odds_value,
                         observed_at, fetched_at, source, data_status)
                        VALUES ('r1', ?, 'win', ?, ?, ?, 'manual_screen', ?)""",
                        (horse, odds, observed, fetched, status))
                add(1, 5.0, "2026-10-25T06:00:00+00:00", "2026-10-25T06:00:05+00:00")
                add(2, 8.0, "2026-10-25T06:01:00+00:00", "2026-10-25T06:01:05+00:00")
                add(3, 2.0, "2026-10-25T06:02:00+00:00", "2026-10-25T06:02:05+00:00", "final")
                add(4, 4.0, "2026-10-25T06:03:00+00:00", "2026-10-25T06:03:05+00:00")
                add(5, -2.0, "2026-10-25T06:01:00+00:00", "2026-10-25T06:01:05+00:00")
                add(6, 6.0, "2026-10-25T06:01:00", "2026-10-25T06:01:05")
            result = audit_odds_history(
                db, as_of_time="2026-10-25T06:02:30+00:00",
                race_starts={"r1": "2026-10-25T06:10:00+00:00"})
            self.assertEqual(result["total_rows"], 6)
            self.assertEqual(result["eligible_rows"], 2)
            self.assertEqual(result["rejected"]["UNKNOWN_OR_FINAL_STATUS"], 1)
            self.assertEqual(result["rejected"]["FUTURE_AS_OF"], 1)
            self.assertEqual(result["rejected"]["INVALID_HORSE_OR_PRICE"], 1)
            self.assertEqual(result["rejected"]["INVALID_TIMESTAMP"], 1)
            self.assertEqual(result["races"][0]["distinct_horses"], 2)
            self.assertFalse(result["asof_certified"])

    def test_after_start_and_naive_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "odds.db"
            with sqlite3.connect(db) as conn:
                conn.execute("""CREATE TABLE odds_timeline (
                    id INTEGER PRIMARY KEY, race_id TEXT, horse_number INTEGER,
                    odds_type TEXT, odds_value REAL, observed_at TEXT,
                    fetched_at TEXT, source TEXT, data_status TEXT)""")
                conn.execute("""INSERT INTO odds_timeline
                    (race_id, horse_number, odds_type, odds_value, observed_at,
                     fetched_at, source, data_status)
                    VALUES ('r1', 1, 'win', 5.0, '2026-10-25T06:00:00+00:00',
                            '2026-10-25T06:01:00+00:00', 'manual', 'manual_prerace')""")
            report = audit_odds_history(
                db, race_starts={"r1": "2026-10-25T06:00:30+00:00"})
            self.assertEqual(report["rejected"]["AFTER_RACE_START"], 1)
            with self.assertRaises(ValueError):
                audit_odds_history(db, as_of_time="2026-10-25T06:02:00")


if __name__ == "__main__":
    unittest.main()
