import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.odds_provider import (
    JVLinkOddsProvider, LicensedFeedOddsProvider,
    FileSnapshotOddsProvider, NetkeibaLiveOddsProvider, OddsProviderManager
)


class ProviderFailClosedTests(unittest.TestCase):
    def test_jvlink_com_does_not_imply_odds_ready(self):
        provider = JVLinkOddsProvider()
        self.assertFalse(provider.check_connection()["connected"])
        self.assertIsNone(provider.fetch_prerace_odds("r1", "2026-10-25T15:40:00+09:00", "pre_10m"))

    def test_directory_does_not_certify_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = LicensedFeedOddsProvider(Path(tmp))
            self.assertFalse(provider.check_connection()["connected"])
            self.assertIsNone(provider.fetch_prerace_odds("r1", "2026-10-25T15:40:00+09:00", "pre_10m"))

    def test_local_files_do_not_certify_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FileSnapshotOddsProvider(Path(tmp))
            self.assertFalse(provider.check_connection()["connected"])
            self.assertIsNone(provider.fetch_prerace_odds("r1", "2026-10-25T15:40:00+09:00", "pre_10m"))

    def test_netkeiba_does_not_probe_network(self):
        provider = NetkeibaLiveOddsProvider(license_confirmed=True)
        self.assertFalse(provider.check_connection()["connected"])
        self.assertIsNone(provider.fetch_prerace_odds("r1", "2026-10-25T15:40:00+09:00", "pre_10m"))

    def test_manager_never_claims_unverified_data(self):
        manager = OddsProviderManager()
        self.assertTrue(manager.diagnose_all_providers()["all_sources_disconnected"])
        with patch("src.odds_provider.time.sleep") as sleep:
            records, audit = manager.fetch_prerace_odds_with_retry(
                "r1", "2026-10-25T15:40:00+09:00", "pre_10m")
        self.assertIsNone(records)
        self.assertEqual(audit["outcome"], "all_sources_disconnected")
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
