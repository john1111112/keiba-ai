import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_background_scan import run_cycle, main


class BackgroundScanTests(unittest.TestCase):
    def test_missing_manifest_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "odds.db"
            result = run_cycle(Path(folder) / "missing.json", db)
            self.assertEqual(result["status"], "NO_UPCOMING_MANIFEST")
            self.assertEqual(result["bet_status"], "NO_BET")
            self.assertFalse(db.exists())

    def test_runner_never_enables_model_or_bets(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text("[]", encoding="utf-8")
            with patch("scripts.run_background_scan.run_schedule_scan",
                       return_value={"status": "COMPLETED", "captured_count": 0}) as scan:
                result = run_cycle(manifest, Path(folder) / "odds.db")
            scan.assert_called_once()
            self.assertEqual(result["bet_status"], "NO_BET")
            self.assertFalse(result["model_inference_allowed"])

    def test_malformed_manifest_returns_blocked_without_db(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "bad.json"
            manifest.write_text("{broken", encoding="utf-8")
            db = Path(folder) / "odds.db"
            result = run_cycle(manifest, db)
            self.assertEqual(result["status"], "SCAN_BLOCKED")
            self.assertEqual(result["bet_status"], "NO_BET")
            self.assertFalse(result["model_inference_allowed"])
            self.assertFalse(db.exists())

    def test_main_writes_status_without_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "status.json"
            status = main(["--manifest", str(Path(folder) / "missing.json"),
                           "--db", str(Path(folder) / "odds.db"),
                           "--out", str(report)])
            self.assertEqual(status, 0)
            saved = __import__("json").loads(report.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "NO_UPCOMING_MANIFEST")
            self.assertIn("checked_at", saved)


if __name__ == "__main__":
    unittest.main()
