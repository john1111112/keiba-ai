from pathlib import Path
import unittest
from unittest.mock import patch
import pandas as pd
from scripts.phase8_win_model_bridge import build_prediction_rows

class FakeWinModel:
    def predict_win_race(self, frame, normalize_to_one=True):
        frame = frame.copy()
        frame['pred_win_prob'] = [0.6, 0.4][:len(frame)]
        return frame

class Phase8Tests(unittest.TestCase):
    def setUp(self):
        self.features = pd.DataFrame([
            {'race_id':'202610200101','horse_number':1},
            {'race_id':'202610200101','horse_number':2}
        ])
        self.args = dict(prediction_at='2026-10-20T06:30:00+09:00',
            cutoff_at='2026-10-20T06:35:00+09:00',
            model_id='win-v2-test',source='verified-feature-snapshot')

    def test_win_probabilities_export(self):
        rows = build_prediction_rows(self.features, FakeWinModel(), **self.args)
        self.assertEqual(len(rows),2)
        self.assertEqual([r['win_probability'] for r in rows],[0.6,0.4])

    def test_duplicate_horse_rejected(self):
        with self.assertRaises(ValueError):
            build_prediction_rows(pd.concat([self.features,self.features.iloc[:1]]),
                                  FakeWinModel(), **self.args)

    def test_missing_win_probabilities_rejected(self):
        class WrongModel:
            def predict_win_race(self, frame, normalize_to_one=True):
                return frame.assign(pred_score=0.8)
        with self.assertRaises(ValueError):
            build_prediction_rows(self.features, WrongModel(), **self.args)

    def test_prediction_after_cutoff_rejected(self):
        args = dict(self.args, prediction_at='2026-10-20T06:40:00+09:00')
        with self.assertRaises(ValueError):
            build_prediction_rows(self.features, FakeWinModel(), **args)

    def test_post_race_finish_rejected(self):
        with self.assertRaises(ValueError):
            build_prediction_rows(self.features.assign(finish_order=[1, 2]),
                                  FakeWinModel(), **self.args)

    def test_post_race_time_rejected(self):
        with self.assertRaises(ValueError):
            build_prediction_rows(self.features.assign(time_str=['1:34.2', '']),
                                  FakeWinModel(), **self.args)

    def test_unconfirmed_placeholders_are_injected(self):
        class CheckModel:
            def predict_win_race(self, frame, normalize_to_one=True):
                assert frame['finish_order'].eq(0).all()
                assert frame['time_str'].eq('').all()
                return frame.assign(pred_win_prob=0.5)
        rows = build_prediction_rows(self.features, CheckModel(), **self.args)
        self.assertEqual(len(rows), 2)

    def test_missing_required_feature_rejected(self):
        model = FakeWinModel()
        model.feature_cols = ['age', 'weight_carry']
        with self.assertRaises(ValueError):
            build_prediction_rows(self.features, model, **self.args)

    def test_changed_horse_identifier_rejected(self):
        class ChangedModel:
            def predict_win_race(self, frame, normalize_to_one=True):
                return frame.assign(horse_number=[1, 3], pred_win_prob=[0.6, 0.4])
        with self.assertRaises(ValueError):
            build_prediction_rows(self.features, ChangedModel(), **self.args)

    def test_probability_sum_rejected(self):
        class BadSumModel:
            def predict_win_race(self, frame, normalize_to_one=True):
                return frame.assign(pred_win_prob=[0.3, 0.3])
        with self.assertRaises(ValueError):
            build_prediction_rows(self.features, BadSumModel(), **self.args)

    def test_invalid_win_probability_rejected(self):
        class BadModel:
            def predict_win_race(self, frame, normalize_to_one=True):
                return frame.assign(pred_win_prob=float('nan'))
        with self.assertRaises(ValueError):
            build_prediction_rows(self.features, BadModel(), **self.args)

    def test_integration_with_phase7_paper_workflow(self):
        """Phase 8 -> Phase 7: prediction CSV export -> capture -> settle -> report integration test"""
        import csv
        import tempfile
        from scripts.phase7_workflow import main as phase7_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pred_path = root / 'predictions.csv'
            odds_path = root / 'odds.csv'
            settle_path = root / 'settlements.csv'
            ledger_path = root / 'paper_ledger.db'

            # 1. Phase 8 export
            rows = build_prediction_rows(self.features, FakeWinModel(), **self.args)
            with open(pred_path, 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=['race_id','horse_number','prediction_at','cutoff_at','win_probability','model_id','source'])
                w.writeheader()
                w.writerows(rows)

            # 2. Pre-race odds CSV
            with open(odds_path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['race_id','horse_number','odds_value','observed_at','fetched_at','race_start_at','source'])
                w.writerow(['202610200101', 1, '2.0', '2026-10-20T06:20:00+09:00', '2026-10-20T06:21:00+09:00', '2026-10-20T06:45:00+09:00', 'sample_odds'])
                w.writerow(['202610200101', 2, '4.0', '2026-10-20T06:20:00+09:00', '2026-10-20T06:21:00+09:00', '2026-10-20T06:45:00+09:00', 'sample_odds'])

            # 3. Phase 7 capture
            cap_res = phase7_main(['capture', '--predictions', str(pred_path), '--odds', str(odds_path), '--ledger', str(ledger_path), '--apply'])
            self.assertEqual(cap_res['applied'], 2)

            # 4. Phase 7 report (unsettled)
            rep1 = phase7_main(['report', '--ledger', str(ledger_path)])
            self.assertEqual(rep1['overall']['unsettled'], 2)
            self.assertIsNone(rep1['overall']['roi'])

            # 5. Phase 7 settle
            with open(settle_path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['race_id','horse_number','prediction_at','won','official_payout_yen','settled_at','source_url','verified_by'])
                w.writerow(['202610200101', 1, '2026-10-20T06:30:00+09:00', 1, 200, '2026-10-20T07:00:00+09:00', 'https://www.jra.go.jp/official', 'auditor_p8'])
                w.writerow(['202610200101', 2, '2026-10-20T06:30:00+09:00', 0, 0, '2026-10-20T07:00:00+09:00', 'https://www.jra.go.jp/official', 'auditor_p8'])
            settle_res = phase7_main(['settle', '--csv', str(settle_path), '--ledger', str(ledger_path), '--apply'])
            self.assertEqual(settle_res['applied'], 2)

            # 6. Phase 7 report (settled)
            rep2 = phase7_main(['report', '--ledger', str(ledger_path)])
            self.assertEqual(rep2['overall']['unsettled'], 0)
            self.assertEqual(rep2['overall']['settled'], 2)
            self.assertEqual(rep2['overall']['roi'], 1.0)

if __name__ == '__main__':
    unittest.main()
