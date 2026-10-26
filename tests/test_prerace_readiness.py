import json
import sqlite3
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

    def test_invalid_prices_and_predictions_never_count(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "odds.db"
            with sqlite3.connect(db) as conn:
                conn.execute("""CREATE TABLE odds_timeline (
                    race_id TEXT, horse_number INTEGER, odds_type TEXT,
                    odds_value REAL, observed_at TEXT, fetched_at TEXT,
                    source TEXT, data_status TEXT)""")
                conn.execute("""CREATE TABLE ai_predictions_timeline (
                    race_id TEXT, horse_number INTEGER, pred_win_prob REAL,
                    predicted_at TEXT, odds_status TEXT)""")
                for horse, price in [(1, -1), (2, 0.5), (3, float("inf"))]:
                    conn.execute("INSERT INTO odds_timeline VALUES (?, ?, 'win', ?, ?, ?, ?, ?)",
                                 ("r1", horse, price, "2026-10-25T15:30:00+09:00",
                                  "2026-10-25T15:31:00+09:00", "manual", "manual_prerace"))
                for horse, prob in [(1, -0.1), (2, 1.5), (3, float("nan"))]:
                    conn.execute("INSERT INTO ai_predictions_timeline VALUES (?, ?, ?, ?, ?)",
                                 ("r1", horse, prob, "2026-10-25T15:32:00+09:00", "captured"))
            result = race_readiness(db, "r1",
                                    as_of_time="2026-10-25T15:35:00+09:00",
                                    race_start_time="2026-10-25T15:40:00+09:00")
            self.assertEqual(result["eligible_odds_horses"], 0)
            self.assertEqual(result["eligible_prediction_rows"], 0)
            self.assertEqual(result["status"], "NO_BET")

    def test_unknown_status_not_counted_even_if_timestamps_look_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "odds.db"
            with sqlite3.connect(db) as conn:
                conn.execute("""CREATE TABLE odds_timeline (
                    race_id TEXT, horse_number INTEGER, odds_type TEXT,
                    odds_value REAL, observed_at TEXT, fetched_at TEXT,
                    source TEXT, data_status TEXT)""")
                conn.execute("""INSERT INTO odds_timeline VALUES
                    ('r1', 1, 'win', 3.0, '2026-10-25T15:30:00+09:00',
                     '2026-10-25T15:31:00+09:00', 'manual', 'unverified_result')""")
            result = race_readiness(db, "r1",
                                    as_of_time="2026-10-25T15:35:00+09:00",
                                    race_start_time="2026-10-25T15:40:00+09:00")
            self.assertEqual(result["eligible_odds_horses"], 0)
            self.assertEqual(result["status"], "NO_BET")

    def test_after_start_rejected(self):
        with self.assertRaises(ValueError):
            race_readiness("missing.db", "r1",
                           as_of_time="2026-10-25T15:41:00+09:00",
                           race_start_time="2026-10-25T15:40:00+09:00")


if __name__ == "__main__":
    unittest.main()
