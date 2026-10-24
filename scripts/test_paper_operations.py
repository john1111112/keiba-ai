import tempfile
import unittest
from pathlib import Path
from src.paper_dashboard import load_dashboard
from src.paper_trading import capture, settle

class OperationalIntegrationTest(unittest.TestCase):
    def test_empty_missing_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(load_dashboard(Path(d)/'missing.db'))

    def test_capture_settle_dashboard_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d)/'paper.db'
            capture(db, [dict(race_id='test-race',horse_number=1,
                prediction_at='2026-10-24T05:00:00+09:00',cutoff_at='2026-10-24T06:00:00+09:00',
                odds_observed_at='2026-10-24T04:40:00+09:00',odds_fetched_at='2026-10-24T04:45:00+09:00',
                win_probability=0.25,odds=5.0,model_id='test-model',source='fixture')])
            before=load_dashboard(db)
            self.assertEqual(before['overall']['unsettled'],1)
            self.assertIsNone(before['overall']['roi'])
            settle(db,[dict(race_id='test-race',horse_number=1,
                prediction_at='2026-10-23T20:00:00+00:00',won=1,official_payout_yen=500,
                settled_at='2026-10-24T07:00:00+09:00',
                source_url='https://www.jra.go.jp/official',verified_by='auditor',verification_status='verified')])
            after=load_dashboard(db)
            self.assertEqual(after['overall']['stake_yen'],100)
            self.assertEqual(after['overall']['returns_yen'],500)
            self.assertEqual(after['overall']['roi'],5)
            self.assertEqual(after['models'][0]['model_id'],'test-model')

if __name__=='__main__': unittest.main()
