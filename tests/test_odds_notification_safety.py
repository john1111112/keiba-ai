"""Regression tests: never notify from missing or stale odds."""
import unittest
from datetime import datetime, timezone

from src.automated_monitor import validate_fresh_race_odds

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
