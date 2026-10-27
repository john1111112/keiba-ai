import copy
import unittest
from datetime import datetime

from scripts.validate_licensed_feed_handoff import validate_feed_handoff


class LicensedFeedHandoffTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.fromisoformat("2026-10-26T10:05:00+09:00")
        self.payload = {
            "race_id": "race-1", "race_start_time": "2026-10-26T15:00:00+09:00",
            "source": "licensed-feed", "license_reference": "contract-reference",
            "observed_at": "2026-10-26T10:00:00+09:00",
            "fetched_at": "2026-10-26T10:01:00+09:00",
            "odds": [{"horse_number": 1, "win_odds": 3.4},
                     {"horse_number": 2, "win_odds": 5.0}]}

    def validate(self, payload=None):
        return validate_feed_handoff(payload or self.payload, now=self.now)

    def test_valid_schema_never_certifies_feed(self):
        result = self.validate()
        self.assertEqual(result["status"], "SCHEMA_VALID_ONLY")
        self.assertFalse(result["license_verified"])
        self.assertFalse(result["provider_connected"])
        self.assertEqual(result["bet_status"], "NO_BET")

    def test_rejects_final_odds(self):
        payload = copy.deepcopy(self.payload)
        payload["race_start_time"] = "2026-10-26T09:00:00+09:00"
        with self.assertRaises(ValueError):
            self.validate(payload)

    def test_rejects_duplicate_horse(self):
        payload = copy.deepcopy(self.payload)
        payload["odds"][1]["horse_number"] = 1
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.validate(payload)

    def test_rejects_spoofed_certification(self):
        payload = copy.deepcopy(self.payload)
        payload["license_verified"] = True
        with self.assertRaisesRegex(ValueError, "Exact"):
            self.validate(payload)

    def test_rejects_future_observation(self):
        payload = copy.deepcopy(self.payload)
        payload["observed_at"] = "2026-10-26T11:00:00+09:00"
        with self.assertRaises(ValueError):
            self.validate(payload)

    def test_rejects_stale_data(self):
        payload = copy.deepcopy(self.payload)
        payload["fetched_at"] = "2026-10-26T09:00:00+09:00"
        payload["observed_at"] = "2026-10-26T08:59:00+09:00"
        with self.assertRaisesRegex(ValueError, "stale"):
            self.validate(payload)

    def test_rejects_naive_timestamps(self):
        payload = copy.deepcopy(self.payload)
        payload["observed_at"] = "2026-10-26T10:00:00"
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.validate(payload)


if __name__ == "__main__":
    unittest.main()
