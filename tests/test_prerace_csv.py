import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from scripts.import_prerace_csv import parse_csv_snapshot, run_csv_pipeline


META = dict(
    race_id="202610260811",
    race_start_time="2026-10-26T15:40:00+09:00",
    observed_at="2026-10-26T15:30:00+09:00",
    fetched_at="2026-10-26T15:30:05+09:00",
    source="manual_screen",
)


class CSVImportTests(unittest.TestCase):
    def test_import_creates_dashboard_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / "odds.csv"
            csv.write_text("horse_number,odds_value\n1,4.5\n2,12.0\n", encoding="utf-8")
            db = Path(folder) / "odds.db"
            html = Path(folder) / "dashboard.html"
            result = run_csv_pipeline(csv, db, html, **META)
            self.assertEqual(result["bet_status"], "NO_BET")
            self.assertEqual(result["import"]["inserted_count"], 2)
            self.assertIn("NO BET", html.read_text(encoding="utf-8"))
            again = run_csv_pipeline(csv, db, html, **META)
            self.assertEqual(again["import"]["skipped_duplicate_count"], 2)
            with closing(sqlite3.connect(db)) as conn, conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM odds_timeline").fetchone()[0], 2)

    def test_dry_run_never_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / "odds.csv"
            csv.write_text("horse_number,odds_value\n1,4.5\n", encoding="utf-8")
            db = Path(folder) / "odds.db"
            html = Path(folder) / "dashboard.html"
            result = run_csv_pipeline(csv, db, html, dry_run=True, **META)
            self.assertEqual(result["status"], "VALIDATED_ONLY")
            self.assertFalse(db.exists())
            self.assertFalse(html.exists())

    def test_no_guessed_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / "odds.csv"
            csv.write_text("horse_number,odds_value\n1,4.5\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_csv_snapshot(csv, **dict(META, fetched_at=""))

    def test_final_odds_timestamp_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / "odds.csv"
            csv.write_text("horse_number,odds_value\n1,4.5\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_csv_snapshot(csv, **dict(META, fetched_at=META["race_start_time"]))

    def test_invalid_csv_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder) / "odds.csv"
            for content in ("horse_number,odds_value\n1,4.5\n1,5.5\n",
                            "horse_number,odds_value\n1,NaN\n",
                            "horse_number,odds_value,final\n1,4.5,yes\n",
                            "horse_number,odds_value\n1\n"):
                with self.subTest(content=content):
                    csv.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        parse_csv_snapshot(csv, **META)


if __name__ == "__main__":
    unittest.main()
