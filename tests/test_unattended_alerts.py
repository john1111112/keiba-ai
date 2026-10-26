import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_unattended_alerts import run_alert_cycle


class UnattendedAlertsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.candidates = self.root / "candidates.json"
        self.state = self.root / "state.db"
        self.now = "2025-10-26T15:20:00+09:00"
        self.good = {
            "race_id": "race-1", "horse_number": 3,
            "race_start_time": "2025-10-26T15:32:00+09:00",
            "odds_observed_at": "2025-10-26T15:19:20+09:00",
            "odds_fetched_at": "2025-10-26T15:19:25+09:00",
            "prediction_created_at": "2025-10-26T15:19:30+09:00",
            "win_odds": 6.2, "win_probability": 0.25,
            "odds_asof_certified": True, "model_asof_certified": True,
            "model_calibrated": True, "source": "jvlink_verified",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rows):
        self.candidates.write_text(json.dumps(rows), encoding="utf-8")

    def test_default_dry_run_has_no_side_effects(self):
        self.write([self.good])
        report = run_alert_cycle(self.candidates, self.state, now=self.now)
        self.assertEqual(report["status"], "DRY_RUN")
        self.assertEqual(report["eligible"], 1)
        self.assertEqual(report["delivered"], 0)
        self.assertFalse(self.state.exists())

    def test_uncertified_never_delivered(self):
        self.write([{**self.good, "model_calibrated": False}])
        with patch("scripts.run_unattended_alerts.sys.platform", "win32"):
            with patch("scripts.run_unattended_alerts._toast") as toast:
                report = run_alert_cycle(self.candidates, self.state, now=self.now, deliver=True)
                self.assertEqual(report["eligible"], 0)
                self.assertEqual(report["delivered"], 0)
                toast.assert_not_called()

    def test_dedupe_across_repeated_runs(self):
        self.write([self.good, self.good])
        with patch("scripts.run_unattended_alerts.sys.platform", "win32"):
            with patch("scripts.run_unattended_alerts._toast") as toast:
                first = run_alert_cycle(self.candidates, self.state, now=self.now, deliver=True)
                second = run_alert_cycle(self.candidates, self.state, now=self.now, deliver=True)
        self.assertEqual(first["delivered"], 1)
        self.assertEqual(first["duplicate_suppressed"], 1)
        self.assertEqual(second["delivered"], 0)
        self.assertEqual(second["duplicate_suppressed"], 2)
        self.assertEqual(toast.call_count, 1)

    def test_missing_feed_fails_closed_without_writing_state(self):
        result = run_alert_cycle(self.candidates, self.state, now=self.now, deliver=False)
        self.assertEqual(result["status"], "NO_CANDIDATE_DATA")
        self.assertEqual(result["bet_status"], "NO_BET")
        self.assertFalse(self.state.exists())

    def test_bad_file_rejected(self):
        self.candidates.write_text('{"not":"a list"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            run_alert_cycle(self.candidates, self.state, now=self.now)

    def test_delivery_failure_is_reported(self):
        self.write([self.good])
        with patch("scripts.run_unattended_alerts.sys.platform", "win32"):
            with patch("scripts.run_unattended_alerts._toast", side_effect=RuntimeError("toast failed")):
                report = run_alert_cycle(self.candidates, self.state, now=self.now, deliver=True)
        self.assertEqual(report["delivery_failures"], 1)
        self.assertEqual(report["delivered"], 0)


if __name__ == "__main__":
    unittest.main()
