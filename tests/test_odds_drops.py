"""Unit tests for the offline odds drop detector."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.detect_odds_drops import detect_drops


class OddsDropTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "odds.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE odds_timeline (
                id INTEGER PRIMARY KEY, race_id TEXT, horse_number INTEGER,
                odds_type TEXT, odds_value REAL, observed_at TEXT,
                fetched_at TEXT, data_status TEXT, source TEXT, timing_tag TEXT)""")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, odds, observed, *, fetched=None, status="pre_race_flash", horse=1):
        with sqlite3.connect(self.db) as conn:
            conn.execute("""INSERT INTO odds_timeline
                (race_id, horse_number, odds_type, odds_value, observed_at,
                 fetched_at, data_status, source, timing_tag)
                VALUES ('race1', ?, 'win', ?, ?, ?, ?, 'manual', 'pre_5m')""",
                (horse, odds, observed, fetched or observed, status))

    def test_detects_large_drop(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(5, "2026-10-25T06:05:00+00:00")
        result = detect_drops(self.db)
        self.assertEqual(len(result["alerts"]), 1)
        self.assertEqual(result["alerts"][0]["drop_pct"], 50)

    def test_final_odds_not_used(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(2, "2026-10-25T06:05:00+00:00", status="final")
        self.assertEqual(detect_drops(self.db)["alerts"], [])

    def test_unverified_timestamp_rejected(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(2, "2026-10-25T06:05:00")
        self.assertEqual(detect_drops(self.db)["alerts"], [])

    def test_late_fetched_snapshot_rejected(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(2, "2026-10-25T06:05:00+00:00",
                 fetched="2026-10-25T06:04:00+00:00")
        self.assertEqual(detect_drops(self.db)["alerts"], [])

    def test_gap_limit(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(2, "2026-10-25T07:00:00+00:00")
        self.assertEqual(detect_drops(self.db)["alerts"], [])

    def test_asof_excludes_later_publication(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(2, "2026-10-25T06:05:00+00:00")
        result = detect_drops(self.db, as_of_time="2026-10-25T06:03:00+00:00")
        self.assertEqual(result["alerts"], [])

    def test_asof_excludes_late_arrival(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(2, "2026-10-25T06:02:00+00:00",
                 fetched="2026-10-25T06:08:00+00:00")
        result = detect_drops(self.db, as_of_time="2026-10-25T06:05:00+00:00")
        self.assertEqual(result["alerts"], [])

    def test_asof_accepts_known_data(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(5, "2026-10-25T06:05:00+00:00")
        result = detect_drops(self.db, as_of_time="2026-10-25T06:06:00+00:00")
        self.assertEqual(len(result["alerts"]), 1)

    def test_asof_rejects_naive_time(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        with self.assertRaises(ValueError):
            detect_drops(self.db, as_of_time="2026-10-25T06:05:00")

    def test_no_db(self):
        self.assertEqual(detect_drops(self.db.parent / "absent.db")["status"], "NO_ODDS_DB")


if __name__ == "__main__":
    unittest.main()
