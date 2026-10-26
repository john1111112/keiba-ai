"""Regression tests: never notify from missing or stale odds."""
import unittest
from datetime import datetime, timezone

from src.automated_monitor import validate_fresh_race_odds, collect_fresh_win_odds, classify_notification_delivery

NOW = datetime(2026, 10, 27, 4, 26, tzinfo=timezone.utc)


def runner(odds, stamp):
    return {"horse_number": 1, "win_odds": odds, "odds_observed_at": stamp}


class OddsNotificationSafetyTest(unittest.TestCase):
    def test_recent_timestamped_quote_is_eligible(self):
        ok, reason = validate_fresh_race_odds(
            [runner(4.2, "2026-10-27T13:25:00+09:00")], NOW, 120
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "FRESH_ODDS_VERIFIED")

    def test_no_timestamp_is_not_eligible(self):
        self.assertFalse(validate_fresh_race_odds([runner(4.2, None)], NOW, 120)[0])

    def test_stale_quote_is_not_eligible(self):
        self.assertFalse(validate_fresh_race_odds(
            [runner(4.2, "2026-10-27T13:20:00+09:00")], NOW, 120
        )[0])

    def test_future_quote_is_not_eligible(self):
        self.assertFalse(validate_fresh_race_odds(
            [runner(4.2, "2026-10-27T13:27:00+09:00")], NOW, 120
        )[0])

    def test_missing_one_horse_blocks_whole_race_notification(self):
        horses = [
            runner(4.2, "2026-10-27T13:25:00+09:00"),
            {"horse_number": 2, "win_odds": None, "odds_observed_at": None}
        ]
        self.assertFalse(validate_fresh_race_odds(horses, NOW, 120)[0])

    def test_missing_unrelated_horse_does_not_block_quoted_buy_candidate(self):
        horses = [
            runner(4.2, "2026-10-27T13:25:00+09:00"),
            {"horse_number": 2, "win_odds": None, "odds_observed_at": None},
        ]
        current = collect_fresh_win_odds(horses, NOW, 120)
        self.assertEqual(current, {1: 4.2})

    def test_only_stale_quotes_are_not_eligible(self):
        horses = [
            runner(4.2, "2026-10-27T13:20:00+09:00"),
            {"horse_number": 2, "win_odds": 10.0,
             "odds_observed_at": "2026-10-27T13:20:00+09:00"},
        ]
        self.assertEqual(collect_fresh_win_odds(horses, NOW, 120), {})

    def test_pc_toast_does_not_count_as_delivered_phone_notification(self):
        self.assertEqual(classify_notification_delivery(
            True, True, "DELIVERED", "FAILED"
        ), "FAILED")
        self.assertEqual(classify_notification_delivery(
            True, True, "DELIVERED", "NO_WEBHOOK"
        ), "FAILED")
        self.assertEqual(classify_notification_delivery(
            True, True, "FAILED", "DELIVERED"
        ), "PARTIAL_SUCCESS")
        self.assertEqual(classify_notification_delivery(
            True, True, "DELIVERED", "DELIVERED"
        ), "DELIVERED")

    def test_windows_only_is_delivered_only_if_toast_succeeds(self):
        self.assertEqual(classify_notification_delivery(
            True, False, "DELIVERED", "NOT_SENT"
        ), "DELIVERED")
        self.assertEqual(classify_notification_delivery(
            True, False, "FAILED", "NOT_SENT"
        ), "FAILED")
        self.assertEqual(classify_notification_delivery(
            False, False, "NOT_SENT", "NOT_SENT"
        ), "NOT_CONFIGURED")

    def test_invalid_number_and_invalid_time_are_not_eligible(self):
        for quote, stamp in [
            ("nan", "2026-10-27T13:25:00+09:00"),
            (0.0, "2026-10-27T13:25:00+09:00"),
            (3.2, "not-a-date"),
            (3.2, "2026-10-27T13:25:00"),
        ]:
            with self.subTest(quote=quote, stamp=stamp):
                self.assertFalse(validate_fresh_race_odds(
                    [runner(quote, stamp)], NOW, 120
                )[0])


if __name__ == "__main__":
    unittest.main()
