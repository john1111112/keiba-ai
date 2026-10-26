import tempfile
import unittest
from pathlib import Path

from src.odds_history import init_odds_history_db, record_odds_snapshot, get_latest_odds_as_of


class OddsAsOfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "odds.db"
        init_odds_history_db(self.db)

    def record(self, price, observed, fetched, status="pre_race_flash"):
        return record_odds_snapshot([{
            "race_id": "r1", "horse_number": 1, "odds_value": price,
            "observed_at": observed, "fetched_at": fetched,
            "source": "test", "data_status": status
        }], db_path=self.db)

    def test_no_naive_timestamps(self):
        self.assertEqual(self.record(4, "2026-10-25T06:00:00", "2026-10-25T06:01:00"), 0)

    def test_no_fabricated_fetch_time(self):
        self.assertEqual(record_odds_snapshot([{
            "race_id": "r1", "horse_number": 1, "odds_value": 3,
            "observed_at": "2026-10-25T06:00:00+00:00",
            "source": "test"
        }], db_path=self.db), 0)

    def test_mixed_offsets_and_asof(self):
        self.record(5, "2026-10-25T15:00:00+09:00", "2026-10-25T15:01:00+09:00")
        self.record(3, "2026-10-25T06:02:00+00:00", "2026-10-25T06:03:00+00:00")
        rows = get_latest_odds_as_of("r1", "2026-10-25T15:02:00+09:00", db_path=self.db)
        self.assertEqual(rows.iloc[0]["odds_value"], 5)
        rows = get_latest_odds_as_of("r1", "2026-10-25T06:04:00+00:00", db_path=self.db)
        self.assertEqual(rows.iloc[0]["odds_value"], 3)

    def test_final_excluded(self):
        self.record(5, "2026-10-25T06:00:00+00:00", "2026-10-25T06:01:00+00:00")
        self.record(2, "2026-10-25T06:02:00+00:00", "2026-10-25T06:03:00+00:00", "final")
        rows = get_latest_odds_as_of("r1", "2026-10-25T06:04:00+00:00", db_path=self.db)
        self.assertEqual(rows.iloc[0]["odds_value"], 5)

    def test_unknown_source_rejected(self):
        self.assertEqual(record_odds_snapshot([{
            "race_id": "r1", "horse_number": 1, "odds_value": 3,
            "observed_at": "2026-10-25T06:00:00+00:00",
            "fetched_at": "2026-10-25T06:01:00+00:00"
        }], db_path=self.db), 0)

    def test_bad_horse_number_rejected(self):
        self.assertEqual(record_odds_snapshot([{
            "race_id": "r1", "horse_number": "invalid", "odds_value": 3,
            "observed_at": "2026-10-25T06:00:00+00:00",
            "fetched_at": "2026-10-25T06:01:00+00:00", "source": "test"
        }], db_path=self.db), 0)

    def test_reject_naive_cutoff(self):
        with self.assertRaises(ValueError):
            get_latest_odds_as_of("r1", "2026-10-25T06:04:00", db_path=self.db)


if __name__ == "__main__":
    unittest.main()
