import csv
import tempfile
import unittest
from pathlib import Path
from src.paper_trading import capture,report
from src.verified_settlement import import_verified_csv

P='2026-10-24T01:00:00+00:00'
T='2026-10-24T02:00:00+00:00'
FIELDS=['race_id','horse_number','prediction_at','won','official_payout_yen','settled_at','source_url','verified_by']
class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'ledger.db';self.csv=Path(self.tmp.name)/'verified.csv'
        capture(self.db,[dict(race_id='R1',horse_number=1,prediction_at=P,cutoff_at=T,win_probability=.3,odds=3.2,odds_observed_at='2026-10-24T00:40:00+00:00',odds_fetched_at='2026-10-24T00:41:00+00:00',model_id='m',source='test')])
    def write(self,rows):
        with self.csv.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
    def row(self,**kw):
        d=dict(race_id='R1',horse_number=1,prediction_at=P,won=1,official_payout_yen=320,settled_at='2026-10-24T03:00:00+00:00',source_url='https://example.org/verified',verified_by='reviewer');d.update(kw);return d
    def test_dry_run_does_not_settle(self):
        self.write([self.row()]);self.assertEqual(import_verified_csv(self.db,self.csv)['applied'],0);self.assertEqual(report(self.db)['settled'],0)
    def test_apply_once(self):
        self.write([self.row()]);self.assertEqual(import_verified_csv(self.db,self.csv,True)['applied'],1);self.assertEqual(report(self.db)['returns_yen'],320)
        with self.assertRaises(ValueError):import_verified_csv(self.db,self.csv,True)
    def test_unknown_prediction_rejected(self):
        self.write([self.row(horse_number=2)])
        with self.assertRaisesRegex(ValueError,'no matching'):import_verified_csv(self.db,self.csv,True)
    def test_batch_atomic_validation(self):
        self.write([self.row(),self.row(horse_number=2)])
        with self.assertRaises(ValueError):import_verified_csv(self.db,self.csv,True)
        self.assertEqual(report(self.db)['settled'],0)
    def test_missing_provenance_rejected(self):
        self.write([self.row(source_url='')])
        with self.assertRaisesRegex(ValueError,'source_url'):import_verified_csv(self.db,self.csv)
    def test_duplicate_rejected(self):
        self.write([self.row(),self.row()])
        with self.assertRaisesRegex(ValueError,'duplicate'):import_verified_csv(self.db,self.csv)
if __name__=='__main__':unittest.main()
