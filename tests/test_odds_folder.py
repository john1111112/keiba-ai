import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.import_odds_folder import ingest_directory


def snapshot(race="r1", price=4.5):
    return {
        "race_id": race, "race_start_time": "2026-10-26T15:40:00+09:00",
        "observed_at": "2026-10-26T15:30:00+09:00",
        "fetched_at": "2026-10-26T15:30:05+09:00",
        "source": "manual_screen",
        "odds": [{"horse_number": 1, "odds_value": price}],
    }


class FolderImporterTests(unittest.TestCase):
    def test_batch_import_and_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            (folder / "a.json").write_text(json.dumps(snapshot("r1")), encoding="utf-8")
            (folder / "b.json").write_text(json.dumps(snapshot("r2")), encoding="utf-8")
            db = root / "odds.db"
            report = ingest_directory(folder, db)
            self.assertEqual(report["inserted_rows"], 2)
            self.assertEqual(report["rejected_files"], 0)
            self.assertEqual(report["bet_status"], "NO_BET")
            again = ingest_directory(folder, db)
            self.assertEqual(again["inserted_rows"], 0)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM odds_timeline").fetchone()[0], 2)

    def test_invalid_file_reported_and_no_fabricated_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            (folder / "bad.json").write_text('{"race_id":', encoding="utf-8")
            (folder / "good.json").write_text(json.dumps(snapshot()), encoding="utf-8")
            report = ingest_directory(folder, root / "odds.db")
            self.assertEqual(report["status"], "PARTIAL_FAILURE")
            self.assertEqual(report["rejected_files"], 1)
            self.assertEqual(report["inserted_rows"], 1)
            self.assertTrue((folder / "bad.json").exists())

    def test_dry_run_creates_no_db(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            (folder / "a.json").write_text(json.dumps(snapshot()), encoding="utf-8")
            db = root / "odds.db"
            report = ingest_directory(folder, db, dry_run=True)
            self.assertEqual(report["inserted_rows"], 0)
            self.assertEqual(report["files"][0]["status"], "VALIDATED")
            self.assertFalse(db.exists())

    def test_conflicting_file_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            db = root / "odds.db"
            (folder / "a.json").write_text(json.dumps(snapshot(price=4.5)), encoding="utf-8")
            self.assertEqual(ingest_directory(folder, db)["inserted_rows"], 1)
            (folder / "b.json").write_text(json.dumps(snapshot(price=2.0)), encoding="utf-8")
            report = ingest_directory(folder, db)
            self.assertEqual(report["rejected_files"], 1)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT odds_value FROM odds_timeline").fetchone()[0], 4.5)


if __name__ == "__main__":
    unittest.main()
