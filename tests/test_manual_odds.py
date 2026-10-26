import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.import_manual_odds import validate_snapshot, import_snapshot


def sample():
    return {
        "race_id": "race1",
        "race_start_time": "2026-10-25T15:40:00+09:00",
        "observed_at": "2026-10-25T15:35:00+09:00",
        "fetched_at": "2026-10-25T15:35:05+09:00",
        "source": "manual_screen",
        "odds": [{"horse_number": 1, "odds_value": 5.0}]
    }


class ManualOddsTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_snapshot(sample()), [(1, 5.0)])

    def test_post_start_rejected(self):
        data = sample()
        data["fetched_at"] = data["race_start_time"]
        with self.assertRaises(ValueError):
            validate_snapshot(data)

    def test_unverified_timezone_rejected(self):
        data = sample()
        data["observed_at"] = "2026-10-25T15:35:00"
        with self.assertRaises(ValueError):
            validate_snapshot(data)

    def test_duplicate_horse_rejected(self):
        data = sample()
        data["odds"].append(dict(data["odds"][0]))
        with self.assertRaises(ValueError):
            validate_snapshot(data)

    def test_import_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "snapshot.json"
            target = Path(folder) / "odds.db"
            source.write_text(json.dumps(sample()), encoding="utf-8")
            self.assertEqual(import_snapshot(source, target)["inserted_count"], 1)
            self.assertEqual(import_snapshot(source, target)["skipped_duplicate_count"], 1)
            with sqlite3.connect(target) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM odds_timeline").fetchone()[0], 1)

    def test_conflicting_odds_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "snapshot.json"
            target = Path(folder) / "odds.db"
            source.write_text(json.dumps(sample()), encoding="utf-8")
            import_snapshot(source, target)
            revised = sample()
            revised["odds"][0]["odds_value"] = 3.0
            source.write_text(json.dumps(revised), encoding="utf-8")
            with self.assertRaises(ValueError):
                import_snapshot(source, target)
            with sqlite3.connect(target) as conn:
                self.assertEqual(conn.execute("SELECT odds_value FROM odds_timeline").fetchone()[0], 5.0)

    def test_fractional_horse_rejected(self):
        data = sample()
        data["odds"][0]["horse_number"] = 1.5
        with self.assertRaises(ValueError):
            validate_snapshot(data)

    def test_blank_source_rejected(self):
        data = sample()
        data["source"] = " "
        with self.assertRaises(ValueError):
            validate_snapshot(data)

    def test_atomic_rollback_on_second_horse_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "snapshot.json"
            target = Path(folder) / "odds.db"
            source.write_text(json.dumps(sample()), encoding="utf-8")
            import_snapshot(source, target)
            revised = sample()
            revised["odds"] = [
                {"horse_number": 2, "odds_value": 10.0},
                {"horse_number": 1, "odds_value": 2.0}
            ]
            source.write_text(json.dumps(revised), encoding="utf-8")
            with self.assertRaises(ValueError):
                import_snapshot(source, target)
            with sqlite3.connect(target) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM odds_timeline").fetchone()[0], 1)

    def test_dry_run_no_db_created(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "snapshot.json"
            target = Path(folder) / "odds.db"
            source.write_text(json.dumps(sample()), encoding="utf-8")
            result = import_snapshot(source, target, dry_run=True)
            self.assertEqual(result["status"], "VALIDATED")
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
