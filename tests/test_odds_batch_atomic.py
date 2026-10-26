import tempfile
import unittest
from pathlib import Path

from src.odds_history import record_odds_snapshot


def row(**changes):
    data = {
        "race_id": "race1", "horse_number": 1, "odds_type": "win",
        "odds_value": 5.0, "observed_at": "2026-10-25T15:30:00+09:00",
        "fetched_at": "2026-10-25T15:30:05+09:00",
        "source": "manual_screen", "data_status": "manual_prerace"
    }
    data.update(changes)
    return data


class OddsBatchAtomicTests(unittest.TestCase):
    def test_invalid_row_never_creates_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "odds.db"
            for invalid in [row(odds_value=float("nan")),
                            row(fetched_at=None),
                            row(data_status="final_settled"),
                            row(horse_number=1.5),
                            row(source="unknown"),
                            row(timing_tag="final_settled")]:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    record_odds_snapshot([row(), invalid], db_path=db)
                self.assertFalse(db.exists())

    def test_duplicate_horse_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "odds.db"
            with self.assertRaises(ValueError):
                record_odds_snapshot([row(), row(odds_value=6)], db_path=db)
            self.assertFalse(db.exists())

    def test_valid_rows_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "odds.db"
            self.assertEqual(record_odds_snapshot([row(), row(horse_number=2)], db_path=db), 2)
            self.assertTrue(db.exists())


if __name__ == "__main__":
    unittest.main()
