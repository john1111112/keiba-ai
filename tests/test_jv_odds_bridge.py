import csv
import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from src.jv_odds_bridge import validate, import_csv

BASE = dict(race_id='202610250811', horse_number='3', odds_value='4.5',
            observed_at='2026-10-25T14:00:00+09:00', fetched_at='2026-10-25T14:01:00+09:00',
            start_at='2026-10-25T15:00:00+09:00', source='sample')
NOW = dt.datetime.fromisoformat('2026-10-25T14:30:00+09:00').astimezone(dt.timezone.utc)

class Tests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate(BASE, now=NOW, allow_sample=True)[1], 3)
    def test_sample_requires_opt_in(self):
        with self.assertRaises(ValueError): validate(BASE, now=NOW)
    def test_future_fetch(self):
        with self.assertRaises(ValueError): validate({**BASE,'fetched_at':'2026-10-25T14:40:00+09:00'},now=NOW,allow_sample=True)
    def test_after_start(self):
        with self.assertRaises(ValueError): validate({**BASE,'start_at':'2026-10-25T13:00:00+09:00'},now=NOW,allow_sample=True)
    def test_no_timezone(self):
        with self.assertRaises(ValueError): validate({**BASE,'observed_at':'2026-10-25T14:00:00'},now=NOW,allow_sample=True)
    def test_bad_odds(self):
        with self.assertRaises(ValueError): validate({**BASE,'odds_value':'nan'},now=NOW,allow_sample=True)
    def test_atomic_reject_and_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            p, db = Path(d)/'odds.csv', Path(d)/'odds.db'
            def write(rows):
                with p.open('w',newline='',encoding='utf8') as f:
                    w=csv.DictWriter(f,fieldnames=BASE);w.writeheader();w.writerows(rows)
            write([BASE,{**BASE,'odds_value':'-1'}])
            with self.assertRaises(ValueError): import_csv(p,db,allow_sample=True,now=NOW)
            self.assertFalse(db.exists())
            write([BASE])
            self.assertEqual(import_csv(p,db,allow_sample=True,now=NOW),1)
            self.assertEqual(import_csv(p,db,allow_sample=True,now=NOW),0)
            con = sqlite3.connect(db)
            try:
                self.assertEqual(con.execute('SELECT data_status FROM odds_timeline').fetchone()[0],'sample_only')
            finally:
                con.close()

if __name__=='__main__': unittest.main()
