import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.import_upcoming_races_csv import import_schedule_csv


class ScheduleImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "races.csv"
        self.dest = self.root / "upcoming.json"
        self.now = datetime.fromisoformat("2026-10-26T10:00:00+09:00")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows, header="race_id,race_status,race_time"):
        self.source.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")

    def run_import(self):
        return import_schedule_csv(self.source, self.dest, now=self.now)

    def test_imports_upcoming_schedule_only(self):
        self.write(["r2,upcoming,2026-10-27T15:00:00+09:00",
                    "r1,cancelled,2026-10-26T09:00:00+09:00"])
        result = self.run_import()
        self.assertEqual(result["status"], "IMPORTED_UNVERIFIED_SCHEDULE")
        self.assertEqual(result["upcoming_count"], 1)
        payload = json.loads(self.dest.read_text(encoding="utf-8"))
        self.assertEqual(set(payload[0]), {"race_id", "race_status", "race_time"})
        self.assertEqual(len(payload), 2)

    def test_results_column_rejected_without_overwriting_existing(self):
        self.dest.write_text("ORIGINAL", encoding="utf-8")
        self.write(["r1,upcoming,2026-10-27T15:00:00+09:00,1"],
                   header="race_id,race_status,race_time,finish_order")
        with self.assertRaisesRegex(ValueError, "headers"):
            self.run_import()
        self.assertEqual(self.dest.read_text(encoding="utf-8"), "ORIGINAL")

    def test_duplicate_race_rejected(self):
        self.write(["r1,upcoming,2026-10-27T15:00:00+09:00",
                    "r1,upcoming,2026-10-27T15:10:00+09:00"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.run_import()
        self.assertFalse(self.dest.exists())

    def test_elapsed_upcoming_race_rejected(self):
        self.write(["r1,upcoming,2026-10-25T15:00:00+09:00"])
        with self.assertRaisesRegex(ValueError, "already started"):
            self.run_import()

    def test_naive_timestamp_rejected(self):
        self.write(["r1,upcoming,2026-10-27T15:00:00"])
        with self.assertRaisesRegex(ValueError, "Timezone"):
            self.run_import()

    def test_missing_column_rejected(self):
        self.write(["r1,upcoming"], header="race_id,race_status")
        with self.assertRaisesRegex(ValueError, "headers"):
            self.run_import()


if __name__ == "__main__":
    unittest.main()
