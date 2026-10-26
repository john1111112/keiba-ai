import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.import_prerace_features_csv import import_race_card_csv, CSV_COLUMNS


class RaceCardCsvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.csv = self.root / "race.csv"
        self.out = self.root / "bundle.json"
        self.row = {
            "horse_number": "1", "bracket_number": "1", "age": "4",
            "weight_carry": "56", "distance": "1600",
            "race_course": "東京", "surface": "芝", "condition": "良", "sex": "牡",
        }
        self.write_rows([self.row])
        self.kwargs = dict(
            race_id="r1",
            race_start_time="2025-10-26T15:40:00+09:00",
            captured_at="2025-10-26T15:25:00+09:00",
            received_at="2025-10-26T15:25:05+09:00",
            source="manual_race_card",
            as_of_time="2025-10-26T15:30:00+09:00",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write_rows(self, rows):
        with self.csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def call(self, **overrides):
        return import_race_card_csv(self.csv, self.out, **(self.kwargs | overrides))

    def test_import_and_idempotency(self):
        first = self.call()
        self.assertTrue(first["written"])
        self.assertFalse(first["model_inference_allowed"])
        self.assertEqual(first["bet_status"], "NO_BET")
        saved = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(saved["features"][0]["horse_number"], 1)
        second = self.call()
        self.assertFalse(second["written"])
        self.assertTrue(second["idempotent"])

    def test_dry_run_never_creates_output(self):
        result = self.call(dry_run=True)
        self.assertFalse(result["written"])
        self.assertFalse(self.out.exists())

    def test_existing_conflicting_bundle_preserved(self):
        self.out.write_text('{"existing":"evidence"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different evidence"):
            self.call()
        self.assertEqual(json.loads(self.out.read_text()), {"existing": "evidence"})

    def test_duplicate_horse_and_invalid_number(self):
        self.write_rows([self.row, self.row])
        with self.assertRaises(ValueError):
            self.call()
        self.assertFalse(self.out.exists())
        self.write_rows([{**self.row, "weight_carry": "nan"}])
        with self.assertRaises(ValueError):
            self.call()

    def test_future_received_time(self):
        with self.assertRaises(ValueError):
            self.call(received_at="2025-10-26T15:35:00+09:00")
        self.assertFalse(self.out.exists())

    def test_extra_column_and_duplicate_headers(self):
        self.csv.write_text(",".join(CSV_COLUMNS) + ",finish_order\n" +
                            ",".join(self.row.values()) + ",1\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.call()
        self.csv.write_text(",".join(CSV_COLUMNS[:-1]) + ",horse_number\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.call()

    def test_no_extra_csv_cells(self):
        self.csv.write_text(",".join(CSV_COLUMNS) + "\n" +
                            ",".join(self.row.values()) + ",bad\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.call()


if __name__ == "__main__":
    unittest.main()
