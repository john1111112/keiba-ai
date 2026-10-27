import json
import tempfile
import unittest
from pathlib import Path

from src.provider_sample_adapter import inspect_provider_sample


class ProviderSampleAdapterTests(unittest.TestCase):
    def test_valid_sample_is_not_certified(self):
        payload = {
            "race_id": "r1",
            "race_start_time": "2026-10-27T15:40:00+09:00",
            "observed_at": "2026-10-27T15:30:00+09:00",
            "fetched_at": "2026-10-27T15:30:05+09:00",
            "source": "licensed_provider_sample",
            "odds": [{"horse_number": 1, "odds_value": 4.5}],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = inspect_provider_sample(path)
        self.assertEqual(result["horse_count"], 1)
        self.assertFalse(result["independently_certified"])
        self.assertFalse(result["eligible_for_live_alerts"])

    def test_post_race_sample_is_rejected(self):
        payload = {
            "race_id": "r1",
            "race_start_time": "2026-10-27T15:40:00+09:00",
            "observed_at": "2026-10-27T15:41:00+09:00",
            "fetched_at": "2026-10-27T15:41:05+09:00",
            "source": "licensed_provider_sample",
            "odds": [{"horse_number": 1, "odds_value": 4.5}],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                inspect_provider_sample(path)


if __name__ == "__main__":
    unittest.main()
