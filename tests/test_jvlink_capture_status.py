import json
import tempfile
import unittest
from pathlib import Path
from src.jvlink_capture_status import build_status, save_status


class StatusTests(unittest.TestCase):
    def test_success_raw_does_not_enable_predictions(self):
        status = build_status({"status": "RAW_CAPTURE_COMPLETE",
            "reports": [{"spec": "0B15", "status": "RAW_RECORDS_STORED_UNPARSED",
                         "received": 5, "inserted": 3}]},
            checked_at="2026-10-27T10:00:00+09:00")
        self.assertEqual(status["raw_records_received"], 5)
        self.assertEqual(status["raw_records_inserted"], 3)
        self.assertFalse(status["notification_ready"])
        self.assertFalse(status["prerace_odds_verified"])
        with tempfile.TemporaryDirectory() as folder:
            path = save_status(Path(folder)/"status.json", status)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), status)

    def test_failure_lists_specs(self):
        status = build_status({"status": "RAW_CAPTURE_PARTIAL_FAILURE",
            "reports": [{"spec": "0B41", "status": "FAILED"}]})
        self.assertEqual(status["failed_specs"], ["0B41"])

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            build_status({"reports": []}, checked_at="2026-10-27T10:00:00")


if __name__ == "__main__":
    unittest.main()
