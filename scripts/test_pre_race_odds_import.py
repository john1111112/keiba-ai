import csv
import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from scripts.import_pre_race_odds import import_csv

NOW = dt.datetime(2026, 10, 24, 6, 0, tzinfo=dt.timezone.utc)
BASE = {'race_id':'race-1','horse_number':'2','odds_value':'3.4',
        'observed_at':'2026-10-24T04:00:00+00:00','fetched_at':'2026-10-24T04:01:00+00:00',
        'race_start_at':'2026-10-24T05:00:00+00:00','source':'licensed-feed-example'}

class OddsImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name)/'odds.csv'
        self.db = Path(self.tmp.name)/'odds.db'

    def write(self, *rows):
        with self.csv.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=BASE.keys())
            writer.writeheader()
            writer.writerows(rows)

    def test_dry_run_does_not_create_db(self):
        self.write(BASE)
        self.assertEqual(import_csv(self.csv,self.db,now=NOW)['validated'],1)
        self.assertFalse(self.db.exists())

    def test_apply_idempotent(self):
        self.write(BASE)
        self.assertEqual(import_csv(self.csv,self.db,True,NOW)['inserted'],1)
        self.assertEqual(import_csv(self.csv,self.db,True,NOW)['already_present'],1)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute('select count(*) from odds_timeline').fetchone()[0],1)

    def test_reject_post_start(self):
        self.write({**BASE,'fetched_at':BASE['race_start_at']})
        with self.assertRaises(ValueError): import_csv(self.csv,self.db,True,NOW)
        self.assertFalse(self.db.exists())

    def test_reject_future_fetched(self):
        self.write({**BASE,'observed_at':'2026-10-24T07:00:00+00:00',
                    'fetched_at':'2026-10-24T07:01:00+00:00',
                    'race_start_at':'2026-10-24T08:00:00+00:00'})
        with self.assertRaises(ValueError): import_csv(self.csv,self.db,True,NOW)

    def test_reject_naive_time(self):
        self.write({**BASE,'observed_at':'2026-10-24T04:00:00'})
        with self.assertRaises(ValueError): import_csv(self.csv,self.db,True,NOW)

    def test_reject_duplicate_csv(self):
        self.write(BASE, BASE)
        with self.assertRaises(ValueError): import_csv(self.csv,self.db,True,NOW)

    def test_all_or_nothing_validation(self):
        self.write(BASE, {**BASE,'horse_number':'-1'})
        with self.assertRaises(ValueError): import_csv(self.csv,self.db,True,NOW)
        self.assertFalse(self.db.exists())

if __name__ == '__main__': unittest.main()
