"""Full offline all-race monitor -> AI EV -> Discord retry test.

These tests simulate authenticated feed and model outputs; they are not
evidence of a live JRA-VAN subscription, SDK, or Discord webhook.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from src.automated_monitor import run_monitor_scan

AS_OF = "2026-10-27T04:26:00+00:00"


def race_feed():
    return {
        "race_id": "2026102705010101",
        "race_time": "2026-10-27T13:45:00+09:00",
        "race_date": "2026-10-27",
        "race_course": "東京",
        "surface": "芝",
        "distance": 1600,
        "condition": "良",
        "direction": "左",
        "race_status": "upcoming",
        "horses": [
            {
                "horse_number": 1, "horse_name": "買い候補",
                "win_odds": 6.0,
                "odds_observed_at": "2026-10-27T13:25:30+09:00"
            },
            {
                "horse_number": 2, "horse_name": "非対象",
                "win_odds": 1.1,
                "odds_observed_at": "2026-10-27T13:25:30+09:00"
            }
        ]
    }


class BackgroundBuySignalE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.feed = self.root / "latest_jvlink_feed.json"
        self.feed.write_text(json.dumps({"races": [race_feed()]}), encoding="utf-8")
        self.db = self.root / "delivery.db"
        self.model_path = self.root / "win_model.joblib"
        self.model_path.write_bytes(b"offline-mock")
        self.cfg = self.root / "config.json"
        self.cfg.write_text(json.dumps({
            "min_ev": 1.15, "min_win_prob": 0.2,
            "max_odds_age_seconds": 720,
            "min_seconds_to_start": 300, "max_seconds_to_start": 1800,
            "windows_toast_enabled": False, "discord_enabled": True,
            "safety_lock_enabled": False, "max_retries": 0,
            "discord_user_id": "123456789012345678"
        }), encoding="utf-8")

    def scan(self):
        return run_monitor_scan(
            self.feed, state_db_path=self.db, config_path=self.cfg,
            as_of_time=AS_OF, model_path=self.model_path
        )

    def dependencies(self):
        model = MagicMock()
        model.predict_win_race.return_value = pd.DataFrame([
            {"horse_number": 1, "horse_name": "買い候補", "pred_win_prob": 0.25},
            {"horse_number": 2, "horse_name": "非対象", "pred_win_prob": 0.75}
        ])
        from contextlib import ExitStack
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch(
            "src.automated_monitor.validate_feed_payload",
            return_value=(True, "VALIDATED", [race_feed()], True)
        ))
        stack.enter_context(patch(
            "src.automated_monitor.KeibaAIWinModel", return_value=model
        ))
        stack.enter_context(patch(
            "src.automated_monitor.build_prerace_28features",
            return_value=pd.DataFrame([
                {"horse_number": 1}, {"horse_number": 2}
            ])
        ))
        return stack

    def test_buy_candidate_discord_delivered_once_even_without_ui(self):
        with self.dependencies(), patch(
            "src.automated_monitor.get_discord_webhook",
            return_value="https://discord.com/api/webhooks/test"
        ), patch("requests.post", return_value=SimpleNamespace(status_code=204)) as post:
            first = self.scan()
            second = self.scan()
        self.assertEqual(first["status"], "COMPLETED")
        self.assertEqual(first["processed_races"], 1)
        self.assertEqual(first["eligible_candidates"], 1)
        self.assertEqual(first["notifications_sent"], 1)
        self.assertEqual(first["unsent_buy_candidates"], 0)
        self.assertEqual(second["notifications_sent"], 0)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["timeout"], 8)
        self.assertEqual(post.call_args.kwargs["json"]["content"], "<@123456789012345678>")

    def test_transient_discord_error_retries_and_deduplicates_after_delivery(self):
        with self.dependencies(), patch(
            "src.automated_monitor.get_discord_webhook",
            return_value="https://discord.com/api/webhooks/test"
        ), patch("requests.post", side_effect=[
            SimpleNamespace(status_code=503), SimpleNamespace(status_code=204)
        ]) as post:
            failed = self.scan()
            retry = self.scan()
            after = self.scan()
        self.assertEqual(failed["notifications_sent"], 0)
        self.assertEqual(failed["unsent_buy_candidates"], 1)
        self.assertEqual(failed["discord_delivery_failures"], 1)
        self.assertEqual(retry["notifications_sent"], 1)
        self.assertEqual(after["notifications_sent"], 0)
        self.assertEqual(post.call_count, 2)

    def test_missing_webhook_is_not_claimed_and_can_send_when_configured(self):
        with self.dependencies(), patch(
            "src.automated_monitor.get_discord_webhook",
            side_effect=["", "https://discord.com/api/webhooks/test",
                         "https://discord.com/api/webhooks/test"]
        ), patch("requests.post", return_value=SimpleNamespace(status_code=204)) as post:
            # The first scan never creates a claim when webhook is absent.
            first = self.scan()
            second = self.scan()
        self.assertEqual(first["eligible_candidates"], 1)
        self.assertEqual(first["missing_discord_webhook"], 1)
        self.assertEqual(first["unsent_buy_candidates"], 1)
        self.assertEqual(second["notifications_sent"], 1)
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
