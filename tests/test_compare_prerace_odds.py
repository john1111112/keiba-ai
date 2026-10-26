"""Regression tests for timestamp-safe odds movement reports."""
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from scripts.compare_prerace_odds import compare_odds


class CompareOddsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "odds.db"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("""CREATE TABLE odds_timeline (
                id INTEGER PRIMARY KEY, race_id TEXT, horse_number INTEGER,
                odds_type TEXT, odds_value REAL, observed_at TEXT,
                fetched_at TEXT, source TEXT, data_status TEXT)""")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, horse, odds, observed, fetched=None, status="manual_prerace"):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("""INSERT INTO odds_timeline
                (race_id, horse_number, odds_type, odds_value, observed_at,
                 fetched_at, source, data_status) VALUES
                ('race1', ?, 'win', ?, ?, ?, 'manual', ?)""",
                (horse, odds, observed, fetched or observed, status))

    def report(self):
        return compare_odds(self.db, "race1",
            earlier="2026-10-27T15:20:00+09:00",
            later="2026-10-27T15:30:00+09:00",
            race_start_time="2026-10-27T15:40:00+09:00")

    def test_shortening(self):
        self.add(1, 5.0, "2026-10-27T15:19:00+09:00")
        self.add(1, 4.0, "2026-10-27T15:29:00+09:00")
        result = self.report()
        self.assertEqual(result["changes"][0]["direction"], "SHORTENED")
        self.assertEqual(result["changes"][0]["change_percent"], -20.0)
        self.assertEqual(result["bet_status"], "NO_BET")

    def test_future_fetch_does_not_leak(self):
        self.add(1, 5.0, "2026-10-27T15:19:00+09:00")
        self.add(1, 4.0, "2026-10-27T15:29:00+09:00",
                 "2026-10-27T15:31:00+09:00")
        self.assertEqual(self.report()["changes"], [])

    def test_settled_odds_excluded(self):
        self.add(1, 5.0, "2026-10-27T15:19:00+09:00")
        self.add(1, 2.0, "2026-10-27T15:29:00+09:00", status="final")
        self.assertEqual(self.report()["changes"], [])

    def test_missing_baseline_is_not_zero(self):
        self.add(2, 3.0, "2026-10-27T15:29:00+09:00")
        result = self.report()
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["missing_earlier"], [2])

    def test_invalid_time_window_rejected(self):
        with self.assertRaises(ValueError):
            compare_odds(self.db, "race1", earlier="2026-10-27T15:30:00+09:00",
                         later="2026-10-27T15:20:00+09:00",
                         race_start_time="2026-10-27T15:40:00+09:00")


if __name__ == "__main__":
    unittest.main()
