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

    def test_self_attested_verified_json_cannot_trigger_notification(self):
        self.write([self.good, self.good])
        with patch("scripts.run_unattended_alerts._toast") as toast:
            result = run_alert_cycle(self.candidates, self.state,
                                     now=self.now, deliver=True)
        self.assertEqual(result["status"], "BLOCKED_UNVERIFIED_PROVENANCE")
        self.assertEqual(result["eligible_by_claim"], 2)
        self.assertEqual(result["delivered"], 0)
        self.assertEqual(result["bet_status"], "NO_BET")
        self.assertFalse(self.state.exists())
        toast.assert_not_called()

    def test_missing_feed_fails_closed_without_writing_state(self):
        result = run_alert_cycle(self.candidates, self.state, now=self.now, deliver=False)
        self.assertEqual(result["status"], "NO_CANDIDATE_DATA")
        self.assertEqual(result["bet_status"], "NO_BET")
        self.assertFalse(self.state.exists())

    def test_bad_file_rejected(self):
        self.candidates.write_text('{"not":"a list"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            run_alert_cycle(self.candidates, self.state, now=self.now)



if __name__ == "__main__":
    unittest.main()
