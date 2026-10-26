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

    def test_unknown_status_not_used(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(2, "2026-10-25T06:05:00+00:00", status="unknown")
        self.assertEqual(detect_drops(self.db)["alerts"], [])

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

    def test_relative_drop_compares_same_window_peers(self):
        self.add(10, "2026-10-25T06:00:00+00:00", horse=1)
        self.add(5, "2026-10-25T06:05:00+00:00", horse=1)
        self.add(8, "2026-10-25T06:00:00+00:00", horse=2)
        self.add(7.2, "2026-10-25T06:05:00+00:00", horse=2)
        alert = detect_drops(self.db)["alerts"][0]
        self.assertEqual(alert["peer_count"], 1)
        self.assertAlmostEqual(alert["peer_avg_drop_pct"], 10)
        self.assertAlmostEqual(alert["relative_drop_pct_points"], 40)

    def test_relative_drop_unavailable_without_peer(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(5, "2026-10-25T06:05:00+00:00")
        alert = detect_drops(self.db)["alerts"][0]
        self.assertEqual(alert["peer_count"], 0)
        self.assertIsNone(alert["relative_drop_pct_points"])

    def test_minutes_before_start(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(5, "2026-10-25T06:05:00+00:00")
        result = detect_drops(self.db, race_start_time="2026-10-25T06:10:00+00:00")
        self.assertEqual(result["alerts"][0]["minutes_before_start"], 5)

    def test_post_start_odds_excluded(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(2, "2026-10-25T06:11:00+00:00")
        result = detect_drops(self.db, race_start_time="2026-10-25T06:10:00+00:00")
        self.assertEqual(result["alerts"], [])

    def test_post_start_fetch_excluded(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(2, "2026-10-25T06:05:00+00:00",
                 fetched="2026-10-25T06:11:00+00:00")
        result = detect_drops(self.db, race_start_time="2026-10-25T06:10:00+00:00")
        self.assertEqual(result["alerts"], [])

    def test_invalid_horse_number_rejected(self):
        self.add(10, "2026-10-25T06:00:00+00:00", horse="invalid")
        self.add(5, "2026-10-25T06:05:00+00:00", horse="invalid")
        result = detect_drops(self.db)
        self.assertEqual(result["alerts"], [])
        self.assertEqual(result["rejected_records"], 2)

    def test_unknown_source_rejected(self):
        self.add(10, "2026-10-25T06:00:00+00:00")
        self.add(5, "2026-10-25T06:05:00+00:00")
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE odds_timeline SET source = 'unknown'")
        result = detect_drops(self.db)
        self.assertEqual(result["alerts"], [])
        self.assertEqual(result["rejected_records"], 2)

    def test_no_db(self):
        self.assertEqual(detect_drops(self.db.parent / "absent.db")["status"], "NO_ODDS_DB")


if __name__ == "__main__":
    unittest.main()
