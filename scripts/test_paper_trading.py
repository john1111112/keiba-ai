import tempfile
import unittest
from pathlib import Path
from src.paper_trading import capture, settle, report, connect

BASE = dict(race_id='202610240101',horse_number=1,prediction_at='2026-10-24T02:00:00+09:00',
 cutoff_at='2026-10-24T02:10:00+09:00',win_probability=.22,odds=5.2,
 odds_observed_at='2026-10-24T01:58:00+09:00',odds_fetched_at='2026-10-24T01:59:00+09:00',model_id='test-model',source='fixture')

class LedgerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/'paper.db'
 def tearDown(self): self.tmp.cleanup()
 def test_capture_and_report(self):
  self.assertEqual(capture(self.db,[BASE]),1)
  self.assertEqual(report(self.db)['unsettled'],1)
  self.assertIsNone(report(self.db)['roi'])
 def test_reject_future_odds(self):
  row={**BASE,'odds_fetched_at':'2026-10-24T02:01:00+09:00'}
  with self.assertRaises(ValueError): capture(self.db,[row])
 def test_reject_naive_timestamp(self):
  with self.assertRaises(ValueError): capture(self.db,[{**BASE,'prediction_at':'2026-10-24T02:00:00'}])
 def test_duplicate_is_atomic(self):
  capture(self.db,[BASE])
  with self.assertRaises(Exception): capture(self.db,[{**BASE,'horse_number':2},BASE])
  self.assertEqual(report(self.db)['predictions'],1)
 def test_settlement_and_roi(self):
  capture(self.db,[BASE]); settle(self.db,[dict(race_id=BASE['race_id'],horse_number=1,prediction_at=BASE['prediction_at'],won=1,official_payout_yen=520,settled_at='2026-10-24T03:00:00+09:00')])
  self.assertEqual(report(self.db)['roi'],5.2)
 def test_no_updates(self):
  capture(self.db,[BASE])
  with connect(self.db) as c:
   with self.assertRaises(Exception): c.execute('DELETE FROM predictions')
 def test_bad_payout(self):
  capture(self.db,[BASE])
  with self.assertRaises(ValueError): settle(self.db,[dict(race_id=BASE['race_id'],horse_number=1,prediction_at=BASE['prediction_at'],won=0,official_payout_yen=100,settled_at='2026-10-24T03:00:00+09:00')])

if __name__=='__main__': unittest.main()
