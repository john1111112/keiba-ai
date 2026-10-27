import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.diagnose_beta_readiness import readiness_report


class ReadinessTests(unittest.TestCase):
    def test_missing_inputs_are_explicitly_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            report = readiness_report(manifest=Path(folder) / "missing.json",
                                      odds_db=Path(folder) / "missing.db")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["bet_status"], "NO_BET")
        self.assertFalse(report["notifications_enabled"])
        self.assertIn("MISSING_UPCOMING_RACES", report["blockers"])
        self.assertIn("NO_VERIFIED_LIVE_ODDS_PROVIDER", report["blockers"])

    def test_existing_files_do_not_certify_live_feed_or_model(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "races.json"
            db = Path(folder) / "odds.db"
            manifest.write_text("[]", encoding="utf-8")
            db.touch()
            report = readiness_report(manifest=manifest, odds_db=db)
        self.assertNotIn("NO_LOCAL_ODDS_HISTORY", report["blockers"])
        self.assertIn("NO_CERTIFIED_PRODUCTION_MODEL", report["blockers"])
        self.assertEqual(report["status"], "BLOCKED")

    def test_fake_provider_connected_does_not_enable_notifications(self):
        with patch("scripts.diagnose_beta_readiness.OddsProviderManager") as manager:
            manager.return_value.diagnose_all_providers.return_value = {
                "status": "SOME_SOURCES_CONNECTED", "all_sources_disconnected": False
            }
            report = readiness_report()
        self.assertFalse(report["notifications_enabled"])
        self.assertIn("NO_TRUSTED_PROVENANCE_TO_ALERT_BRIDGE", report["blockers"])
        self.assertEqual(report["bet_status"], "NO_BET")


if __name__ == "__main__":
    unittest.main()
