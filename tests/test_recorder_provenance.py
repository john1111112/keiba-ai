import unittest
import tempfile
from pathlib import Path
import sqlite3
from datetime import datetime

from src.prerace_recorder import validate_odds_records, record_prerace_snapshot


def odds(**updates):
    row = {"race_id": "r1", "horse_number": 1, "odds_value": 4.0,
           "observed_at": "2026-10-25T15:30:00+09:00",
           "fetched_at": "2026-10-25T15:30:01+09:00",
           "source": "test", "data_status": "pre_race_flash"}
    row.update(updates)
    return row


class RecorderProvenanceTests(unittest.TestCase):
    def check(self, rows):
        return validate_odds_records(rows,
            "2026-10-25T15:40:00+09:00",
            datetime.fromisoformat("2026-10-25T15:35:00+09:00"))

    def test_missing_fetch_time_rejected(self):
        self.assertEqual(self.check([odds(fetched_at=None)]), [])

    def test_naive_observed_rejected(self):
        self.assertEqual(self.check([odds(observed_at="2026-10-25T15:30:00")]), [])

    def test_invalid_timestamp_rejected(self):
        self.assertEqual(self.check([odds(observed_at="invalid")]), [])

    def test_missing_source_rejected(self):
        self.assertEqual(self.check([odds(source="unknown")]), [])

    def test_missing_horse_rejected(self):
        self.assertEqual(self.check([odds(horse_number=None)]), [])

    def test_post_start_execution_cannot_write_db(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "odds.db"
            with self.assertRaises(ValueError):
                record_prerace_snapshot(
                    "r1", "2026-10-25T15:40:00+09:00", "pre_5m",
                    executed_at="2026-10-25T15:41:00+09:00",
                    db_path=db)
            self.assertFalse(db.exists())

    def test_future_target_cannot_write_db(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "odds.db"
            with self.assertRaises(ValueError):
                record_prerace_snapshot(
                    "r1", "2026-10-25T15:40:00+09:00", "pre_5m",
                    executed_at="2026-10-25T15:30:00+09:00",
                    db_path=db)
            self.assertFalse(db.exists())

    def test_fractional_horse_rejected(self):
        self.assertEqual(self.check([odds(horse_number=1.5)]), [])

    def test_non_win_odds_rejected(self):
        self.assertEqual(self.check([odds(odds_type="place")]), [])

    def test_mixed_race_snapshot_not_partially_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "odds.db"
            result = record_prerace_snapshot(
                "r1", "2026-10-25T15:40:00+09:00", "pre_5m",
                odds_records=[odds(), odds(race_id="r2", horse_number=2)],
                executed_at="2026-10-25T15:35:00+09:00", db_path=db)
            self.assertEqual(result["odds_status"], "error")
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM odds_timeline").fetchone()[0], 0)

    def test_duplicate_horse_snapshot_not_partially_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "odds.db"
            result = record_prerace_snapshot(
                "r1", "2026-10-25T15:40:00+09:00", "pre_5m",
                odds_records=[odds(), odds(odds_value=5.0)],
                executed_at="2026-10-25T15:35:00+09:00", db_path=db)
            self.assertEqual(result["odds_status"], "error")
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM odds_timeline").fetchone()[0], 0)

    def test_late_target_cannot_write_db(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "odds.db"
            with self.assertRaises(ValueError):
                record_prerace_snapshot(
                    "r1", "2026-10-25T15:40:00+09:00", "pre_30m",
                    executed_at="2026-10-25T15:35:00+09:00", db_path=db)
            self.assertFalse(db.exists())

    def test_valid_record_kept(self):
        self.assertEqual(len(self.check([odds()])), 1)

    def test_future_fetch_rejected(self):
        self.assertEqual(self.check([odds(fetched_at="2026-10-25T15:36:00+09:00")]), [])


if __name__ == "__main__":
    unittest.main()
