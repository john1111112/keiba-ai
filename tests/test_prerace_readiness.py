import json
import tempfile
import unittest
from pathlib import Path

from scripts.import_manual_odds import import_snapshot
from scripts.prerace_readiness import race_readiness


class ReadinessTests(unittest.TestCase):
    def test_missing_db_no_bet(self):
        with tempfile.TemporaryDirectory() as directory:
            result = race_readiness(
                Path(directory) / "missing.db", "r1",
                as_of_time="2026-10-25T15:35:00+09:00",
                race_start_time="2026-10-25T15:40:00+09:00")
            self.assertEqual(result["status"], "NO_BET")

    def test_manual_odds_never_certified(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "snapshot.json"
            db = folder / "odds.db"
            source.write_text(json.dumps({
                "race_id": "r1",
                "race_start_time": "2026-10-25T15:40:00+09:00",
                "observed_at": "2026-10-25T15:30:00+09:00",
                "fetched_at": "2026-10-25T15:30:05+09:00",
                "source": "manual_screen",
                "odds": [{"horse_number": 1, "odds_value": 5.0}]
            }), encoding="utf-8")
            import_snapshot(source, db)
            result = race_readiness(db, "r1",
                                    as_of_time="2026-10-25T15:35:00+09:00",
                                    race_start_time="2026-10-25T15:40:00+09:00")
            self.assertEqual(result["status"], "NO_BET")
            self.assertEqual(result["eligible_odds_horses"], 1)
            self.assertEqual(result["manual_or_uncertified_odds"], 1)

    def test_after_start_rejected(self):
        with self.assertRaises(ValueError):
            race_readiness("missing.db", "r1",
                           as_of_time="2026-10-25T15:41:00+09:00",
                           race_start_time="2026-10-25T15:40:00+09:00")


if __name__ == "__main__":
    unittest.main()
