import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.race_odds_timeline import race_odds_timeline


class RaceOddsTimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "odds.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE odds_timeline (
                id INTEGER PRIMARY KEY, race_id TEXT, horse_number INTEGER,
                odds_type TEXT, odds_value REAL, observed_at TEXT,
                fetched_at TEXT, source TEXT, data_status TEXT)""")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, horse, price, observed, fetched, *, status="manual_prerace", source="manual_screen"):
        with sqlite3.connect(self.db) as conn:
            conn.execute("""INSERT INTO odds_timeline
                (race_id, horse_number, odds_type, odds_value, observed_at,
                 fetched_at, source, data_status)
                VALUES ('r1', ?, 'win', ?, ?, ?, ?, ?)""",
                (horse, price, observed, fetched, source, status))

    def report(self):
        return race_odds_timeline(
            self.db, "r1", as_of_time="2026-10-26T06:10:00+00:00",
            race_start_time="2026-10-26T06:15:00+00:00")

    def test_asof_and_final_exclusion(self):
        self.add(1, 8, "2026-10-26T06:00:00+00:00", "2026-10-26T06:00:05+00:00")
        self.add(1, 4, "2026-10-26T06:05:00+00:00", "2026-10-26T06:11:00+00:00")
        self.add(1, 2, "2026-10-26T06:06:00+00:00", "2026-10-26T06:06:05+00:00", status="final")
        self.add(1, 3, "2026-10-26T06:20:00+00:00", "2026-10-26T06:20:05+00:00")
        result = self.report()
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["observations"][0]["horses"][0]["win_odds"], 8)
        self.assertEqual(result["rejected_rows"], 3)
        self.assertFalse(result["asof_certified"])
        self.assertEqual(result["bet_status"], "NO_BET")

    def test_earliest_arrival_for_same_publication(self):
        self.add(1, 7, "2026-10-26T06:00:00+00:00", "2026-10-26T06:00:10+00:00")
        self.add(1, 8, "2026-10-26T06:00:00+00:00", "2026-10-26T06:00:05+00:00")
        self.add(2, 12, "2026-10-26T06:00:00+00:00", "2026-10-26T06:00:05+00:00")
        result = self.report()
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual([r["win_odds"] for r in result["observations"][0]["horses"]], [8, 12])

    def test_timezone_required(self):
        with self.assertRaises(ValueError):
            race_odds_timeline(self.db, "r1", as_of_time="2026-10-26T06:10:00",
                               race_start_time="2026-10-26T06:15:00+00:00")

    def test_missing_database_no_bet(self):
        result = race_odds_timeline(Path(self.temp.name) / "missing.db", "r1",
                                    as_of_time="2026-10-26T06:10:00+00:00",
                                    race_start_time="2026-10-26T06:15:00+00:00")
        self.assertEqual(result["status"], "NO_ODDS_DB")


if __name__ == "__main__":
    unittest.main()
