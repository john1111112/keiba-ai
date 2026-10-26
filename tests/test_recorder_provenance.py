import unittest
from datetime import datetime

from src.prerace_recorder import validate_odds_records


def odds(**updates):
    row = {"race_id": "r1", "horse_number": 1, "odds_value": 4.0,
           "observed_at": "2026-10-25T15:30:00+09:00",
           "fetched_at": "2026-10-25T15:30:01+09:00",
           "source": "test", "data_status": "pre_race_flash"}
    row.update(updates)
    return row


class RecorderProvenanceTests(unittest.TestCase):
    def check(self, rows):
        return validate_odds_records(rows,
            "2026-10-25T15:40:00+09:00",
            datetime.fromisoformat("2026-10-25T15:35:00+09:00"))

    def test_missing_fetch_time_rejected(self):
        self.assertEqual(self.check([odds(fetched_at=None)]), [])

    def test_naive_observed_rejected(self):
        self.assertEqual(self.check([odds(observed_at="2026-10-25T15:30:00")]), [])

    def test_invalid_timestamp_rejected(self):
        self.assertEqual(self.check([odds(observed_at="invalid")]), [])

    def test_missing_source_rejected(self):
        self.assertEqual(self.check([odds(source="unknown")]), [])

    def test_missing_horse_rejected(self):
        self.assertEqual(self.check([odds(horse_number=None)]), [])

    def test_valid_record_kept(self):
        self.assertEqual(len(self.check([odds()])), 1)

    def test_future_fetch_rejected(self):
        self.assertEqual(self.check([odds(fetched_at="2026-10-25T15:36:00+09:00")]), [])


if __name__ == "__main__":
    unittest.main()
