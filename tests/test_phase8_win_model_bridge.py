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

if __name__ == '__main__':
    unittest.main()
