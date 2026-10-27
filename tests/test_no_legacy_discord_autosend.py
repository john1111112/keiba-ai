"""Prevent unverified UI-triggered or mass-ping Discord notifications.

A dashboard rerun must never send buy recommendations from the legacy
3rd-place model or historical feature cache. Only the signed JV-Link monitor
owns background buy candidate notifications.
"""
from pathlib import Path
import unittest
from unittest.mock import patch

from src.notifier import send_discord_test


ROOT = Path(__file__).resolve().parents[1]


class DiscordDashboardSafetyTests(unittest.TestCase):
    def test_dashboard_never_invokes_legacy_batch_alerts(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("notify_all_predicted_ss_s_races(df_feat, model)", source)
        self.assertNotIn("notify_all_predicted_ss_s_races(df_feat_cur, m", source)
        self.assertIn("KeibaAI_BackgroundMonitor", source)

    def test_notifier_does_not_mass_mention_everyone(self):
        source = (ROOT / "src" / "notifier.py").read_text(encoding="utf-8")
        self.assertNotIn("@everyone", source)
        self.assertNotIn('"parse": ["everyone"', source)

    def test_discord_test_uses_suppressed_mentions(self):
        with patch("src.notifier.requests.post") as post:
            post.return_value.status_code = 204
            outcome = send_discord_test("https://discord.com/api/webhooks/1234/FakeTOKEN")
        self.assertTrue(outcome["success"])
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("@everyone", payload["content"])
        self.assertEqual(payload["allowed_mentions"], {"parse": []})


if __name__ == "__main__":
    unittest.main()
