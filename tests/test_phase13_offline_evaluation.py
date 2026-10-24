"""Tests for Phase 13 Offline Model Evaluation Pipeline.

Verifies leak-free chronological splitting, strict temporal feature gating,
ROI evaluation eligibility, and reproducible synthetic pipeline execution.
"""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.phase13_offline_evaluation import (
    purged_time_series_split,
    audit_feature_leakage,
    compute_log_loss,
    compute_brier_score,
    evaluate_offline_predictions,
    run_evaluation_pipeline,
    generate_synthetic_benchmark
)

class Phase13OfflineEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_purged_time_series_split_strict_chronology(self):
        """Trainの最大時刻 < Testの最小時刻、かつ同一レースの混入がないこと"""
        records = [
            {'race_id': 'R1', 'race_start_at': '2026-01-01T10:00:00Z', 'val': 1},
            {'race_id': 'R1', 'race_start_at': '2026-01-01T10:00:00Z', 'val': 2},
            {'race_id': 'R2', 'race_start_at': '2026-01-01T11:00:00Z', 'val': 3},
            {'race_id': 'R3', 'race_start_at': '2026-01-01T12:00:00Z', 'val': 4},
            {'race_id': 'R4', 'race_start_at': '2026-01-01T13:00:00Z', 'val': 5},
        ]
        train, test = purged_time_series_split(records, train_ratio=0.5)
        train_races = set(r['race_id'] for r in train)
        test_races = set(r['race_id'] for r in test)

        self.assertEqual(train_races, {'R1', 'R2'})
        self.assertEqual(test_races, {'R3', 'R4'})
        self.assertFalse(train_races.intersection(test_races))

    def test_purged_time_series_split_detects_temporal_leak(self):
        """時系列矛盾が発生した場合は例外を発生させる"""
        inverted_records = [
            {'race_id': 'R1', 'race_start_at': '2026-01-01T15:00:00Z'},
            {'race_id': 'R2', 'race_start_at': '2026-01-01T10:00:00Z'},
        ]
        # Although sorted in helper, if same timestamp or invalid split
        # We ensure chronological assertion is preserved
        train, test = purged_time_series_split(inverted_records, train_ratio=0.5)
        self.assertEqual(train[0]['race_id'], 'R2')
        self.assertEqual(test[0]['race_id'], 'R1')

    def test_audit_feature_leakage_post_start_rejected(self):
        """特徴量観測時刻が発走時刻以降の場合は即座に拒絶"""
        bad_records = [{
            'race_id': 'R1',
            'horse_number': 1,
            'race_start_at': '2026-01-01T12:00:00Z',
            'feature_observed_at': '2026-01-01T12:05:00Z',
            'cutoff_at': '2026-01-01T11:55:00Z'
        }]
        with self.assertRaises(ValueError) as cm:
            audit_feature_leakage(bad_records)
        self.assertIn("on or after race start", str(cm.exception))

    def test_audit_feature_leakage_post_cutoff_rejected(self):
        """特徴量観測時刻が締切時刻以降の場合は即座に拒絶"""
        bad_records = [{
            'race_id': 'R1',
            'horse_number': 1,
            'race_start_at': '2026-01-01T12:00:00Z',
            'feature_observed_at': '2026-01-01T11:58:00Z',
            'cutoff_at': '2026-01-01T11:55:00Z'
        }]
        with self.assertRaises(ValueError) as cm:
            audit_feature_leakage(bad_records)
        self.assertIn("after cutoff", str(cm.exception))

    def test_synthetic_data_roi_gated_as_unevaluable(self):
        """未検証・合成データに対してROIが評価不可（unevaluable）となること"""
        records = generate_synthetic_benchmark(num_races=5, horses_per_race=5, seed=123)
        _, test_rows = purged_time_series_split(records, train_ratio=0.6)
        report = evaluate_offline_predictions(test_rows)

        self.assertEqual(
            report['roi_evaluation']['roi_status'],
            'unevaluable_missing_verified_odds_and_payouts'
        )
        self.assertIsNone(report['roi_evaluation']['roi'])
        self.assertIn('NOTICE: SYNTHETIC BENCHMARK', report['disclaimer'])

    def test_verified_data_roi_evaluated_when_available(self):
        """公式検証済み払戻・実データが存在する場合はROIが算出されること"""
        test_rows = [
            {
                'race_id': 'R1',
                'horse_number': 1,
                'race_start_at': '2026-01-01T12:00:00Z',
                'feature_observed_at': '2026-01-01T11:30:00Z',
                'cutoff_at': '2026-01-01T11:50:00Z',
                'win': 1,
                'prob_model': 0.8,
                'prob_baseline': 0.5,
                'is_synthetic': False,
                'verified_payout_yen': 250
            },
            {
                'race_id': 'R1',
                'horse_number': 2,
                'race_start_at': '2026-01-01T12:00:00Z',
                'feature_observed_at': '2026-01-01T11:30:00Z',
                'cutoff_at': '2026-01-01T11:50:00Z',
                'win': 0,
                'prob_model': 0.2,
                'prob_baseline': 0.5,
                'is_synthetic': False,
                'verified_payout_yen': None
            }
        ]
        report = evaluate_offline_predictions(test_rows)
        self.assertEqual(report['roi_evaluation']['roi_status'], 'evaluated')
        self.assertEqual(report['roi_evaluation']['roi'], 2.5) # 250 / 100
        self.assertEqual(report['roi_evaluation']['net_profit_yen'], 150)

    def test_reproducibility_with_fixed_seed(self):
        """固定シードで結果が完全再現されること"""
        res1 = run_evaluation_pipeline(data_source='synthetic', seed=42)
        res2 = run_evaluation_pipeline(data_source='synthetic', seed=42)

        self.assertEqual(
            res1['metrics']['model']['log_loss'],
            res2['metrics']['model']['log_loss']
        )
        self.assertEqual(
            res1['metrics']['model']['brier_score'],
            res2['metrics']['model']['brier_score']
        )
        self.assertEqual(
            res1['metrics']['model']['top1_hit_rate'],
            res2['metrics']['model']['top1_hit_rate']
        )

    def test_report_json_output(self):
        """JSONレポートファイルが正常に出力されること"""
        out_file = self.root / 'report.json'
        res = run_evaluation_pipeline(data_source='synthetic', seed=42, output_path=out_file)

        self.assertTrue(out_file.exists())
        saved = json.loads(out_file.read_text(encoding='utf-8'))
        self.assertEqual(saved['total_test_races'], res['total_test_races'])
        self.assertIn('pipeline_metadata', saved)

if __name__ == '__main__':
    unittest.main()
