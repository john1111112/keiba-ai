import csv
import tempfile
import unittest
from pathlib import Path
from scripts.phase7_workflow import plan_capture, main

def write_csv(path, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

class Phase7WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.pred, self.odds, self.ledger = root/'pred.csv', root/'odds.csv', root/'ledger.db'
        self.predrow = dict(race_id='202610200101', horse_number=3,
            prediction_at='2026-10-20T06:30:00+09:00',
            cutoff_at='2026-10-20T06:35:00+09:00',
            win_probability='0.3', model_id='test_model', source='test')
        self.oddsrow = dict(race_id='202610200101', horse_number=3,
            odds_value='4.5', observed_at='2026-10-20T06:20:00+09:00',
            fetched_at='2026-10-20T06:21:00+09:00',
            race_start_at='2026-10-20T06:50:00+09:00', source='test')
        write_csv(self.pred, [self.predrow])
        write_csv(self.odds, [self.oddsrow])

    def test_plan(self):
        rows = plan_capture(self.pred, self.odds)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['odds'], 4.5)

    def test_capture_dry_run_does_not_create_db(self):
        main(['capture','--predictions',str(self.pred),'--odds',str(self.odds),
              '--ledger',str(self.ledger)])
        self.assertFalse(self.ledger.exists())

    def test_apply_duplicate_rejected(self):
        args = ['capture','--predictions',str(self.pred),'--odds',str(self.odds),
                '--ledger',str(self.ledger),'--apply']
        main(args)
        self.assertTrue(self.ledger.exists())
        with self.assertRaises(Exception):
            main(args)

    def test_missing_start_rejected(self):
        write_csv(self.odds, [dict(self.oddsrow, race_start_at='')])
        with self.assertRaises(ValueError):
            plan_capture(self.pred, self.odds)

    def test_unknown_observation_rejected(self):
        write_csv(self.odds, [dict(self.oddsrow, observed_at='')])
        with self.assertRaises(ValueError):
            plan_capture(self.pred, self.odds)

    def test_future_snapshot_rejected(self):
        write_csv(self.odds, [dict(self.oddsrow, fetched_at='2026-10-20T06:40:00+09:00')])
        with self.assertRaises(ValueError):
            plan_capture(self.pred, self.odds)

    def test_end_to_end_paper_workflow_lifecycle(self):
        """Phase 7: capture(dry-run/apply) -> report(unsettled) -> settle(apply) -> report(settled) lifecycle test"""
        # 1. capture dry-run
        res_dry = main(['capture', '--predictions', str(self.pred), '--odds', str(self.odds),
                        '--ledger', str(self.ledger)])
        self.assertEqual(res_dry['mode'], 'dry-run')
        self.assertEqual(res_dry['validated'], 1)
        self.assertEqual(res_dry['applied'], 0)
        self.assertFalse(self.ledger.exists())

        # 2. capture apply
        res_apply = main(['capture', '--predictions', str(self.pred), '--odds', str(self.odds),
                          '--ledger', str(self.ledger), '--apply'])
        self.assertEqual(res_apply['mode'], 'apply')
        self.assertEqual(res_apply['validated'], 1)
        self.assertEqual(res_apply['applied'], 1)
        self.assertTrue(self.ledger.exists())

        # 3. report (未精算状態)
        rep1 = main(['report', '--ledger', str(self.ledger)])
        self.assertEqual(rep1['overall']['predictions'], 1)
        self.assertEqual(rep1['overall']['unsettled'], 1)
        self.assertEqual(rep1['overall']['settled'], 0)
        self.assertIsNone(rep1['overall']['roi'])

        # 4. settle apply (公式検証済み精算CSV)
        settle_csv = Path(self.temp.name) / 'verified_settlement.csv'
        write_csv(settle_csv, [dict(
            race_id='202610200101', horse_number=3,
            prediction_at='2026-10-20T06:30:00+09:00',
            won=1, official_payout_yen=450,
            settled_at='2026-10-20T07:00:00+09:00',
            source_url='https://www.jra.go.jp/official',
            verified_by='auditor_phase7'
        )])
        res_settle = main(['settle', '--csv', str(settle_csv), '--ledger', str(self.ledger), '--apply'])
        self.assertEqual(res_settle['mode'], 'apply')
        self.assertEqual(res_settle['applied'], 1)

        # 5. report (精算完了・回収率計算状態)
        rep2 = main(['report', '--ledger', str(self.ledger)])
        self.assertEqual(rep2['overall']['predictions'], 1)
        self.assertEqual(rep2['overall']['settled'], 1)
        self.assertEqual(rep2['overall']['unsettled'], 0)
        self.assertEqual(rep2['overall']['stake_yen'], 100)
        self.assertEqual(rep2['overall']['returns_yen'], 450)
        self.assertEqual(rep2['overall']['profit_yen'], 350)
        self.assertEqual(rep2['overall']['roi'], 4.5)

if __name__ == '__main__':
    unittest.main()
