"""Tests for Phase 16 Paper Quality Gates & Robustness Diagnostics.

Verifies strict invariant evaluation, explicit reason codes,
card-level probability sum & duplicate checks, and bootstrap intervals.
"""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.phase16_paper_quality_gate import (
    evaluate_runner_quality,
    evaluate_race_card_quality,
    compute_bootstrap_confidence_intervals,
    run_phase16_pipeline,
    QualityReasonCode
)

class Phase16PaperQualityGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Baseline valid runner
        self.valid_runner = {
            'race_id': 'R1',
            'horse_number': 1,
            'source_provenance': 'synthetic_benchmark',
            'model_id': 'win-v2',
            'prob_raw': 0.25,
            'odds_value': 4.0,
            'feature_observed_at': '2026-01-01T11:30:00Z',
            'cutoff_at': '2026-01-01T11:45:00Z',
            'prediction_at': '2026-01-01T11:50:00Z',
            'odds_observed_at': '2026-01-01T11:48:00Z',
            'race_start_at': '2026-01-01T12:00:00Z'
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_runner_passes(self):
        """正常なランナーレコードは PASSED"""
        code = evaluate_runner_quality(self.valid_runner)
        self.assertEqual(code, QualityReasonCode.PASSED)

    def test_invalid_source_provenance_rejected(self):
        """不正な出所・無断収集データは拒絶"""
        bad = dict(self.valid_runner, source_provenance='unauthorized_scraping')
        self.assertEqual(evaluate_runner_quality(bad), QualityReasonCode.INVALID_SOURCE_PROVENANCE)

    def test_post_start_feature_observation_rejected(self):
        """発走時刻以降の特徴量は拒絶"""
        bad = dict(self.valid_runner, feature_observed_at='2026-01-01T12:05:00Z')
        self.assertEqual(evaluate_runner_quality(bad), QualityReasonCode.POST_START_FEATURE_OBSERVATION)

    def test_feature_cutoff_violation_rejected(self):
        """締切時刻以降の特徴量は拒絶"""
        bad = dict(self.valid_runner, feature_observed_at='2026-01-01T11:46:00Z')
        self.assertEqual(evaluate_runner_quality(bad), QualityReasonCode.FEATURE_CUTOFF_VIOLATION)

    def test_post_start_odds_observation_rejected(self):
        """発走時刻以降のオッズ観測は拒絶"""
        bad = dict(self.valid_runner, odds_observed_at='2026-01-01T12:00:00Z')
        self.assertEqual(evaluate_runner_quality(bad), QualityReasonCode.POST_START_ODDS_OBSERVATION)

    def test_observation_after_prediction_rejected(self):
        """予測生成より後のオッズ観測は拒絶"""
        bad = dict(self.valid_runner, odds_observed_at='2026-01-01T11:55:00Z')
        self.assertEqual(evaluate_runner_quality(bad), QualityReasonCode.OBSERVATION_AFTER_PREDICTION)

    def test_invalid_odds_value_rejected(self):
        """1.0未満やNaNのオッズは拒絶"""
        for bad_odds in [0.9, -1.0, float('nan'), None]:
            with self.subTest(bad_odds=bad_odds):
                bad = dict(self.valid_runner, odds_value=bad_odds)
                self.assertEqual(evaluate_runner_quality(bad), QualityReasonCode.INVALID_OR_MISSING_ODDS)

    def test_invalid_probability_value_rejected(self):
        """0〜1の範囲外やNaNの確率は拒絶"""
        for bad_p in [1.5, -0.1, float('nan'), None]:
            with self.subTest(bad_p=bad_p):
                bad = dict(self.valid_runner, prob_raw=bad_p)
                self.assertEqual(evaluate_runner_quality(bad), QualityReasonCode.INVALID_PROBABILITY_VALUE)

    def test_missing_model_version_rejected(self):
        """モデル版欠損は拒絶"""
        bad = dict(self.valid_runner, model_id='')
        self.assertEqual(evaluate_runner_quality(bad), QualityReasonCode.MISSING_MODEL_VERSION)

    def test_race_card_duplicate_horse_number_rejected(self):
        """レース内で馬番が重複した場合は拒絶"""
        r1 = dict(self.valid_runner, horse_number=1)
        r2 = dict(self.valid_runner, horse_number=1)
        passed, code, _ = evaluate_race_card_quality('R1', [r1, r2])
        self.assertFalse(passed)
        self.assertEqual(code, QualityReasonCode.DUPLICATE_HORSE_NUMBER)

    def test_race_card_probability_sum_anomaly_rejected(self):
        """レース全体の確率総和が1.0から大きく逸脱した場合は拒絶"""
        r1 = dict(self.valid_runner, horse_number=1, prob_raw=0.1)
        r2 = dict(self.valid_runner, horse_number=2, prob_raw=0.1)
        passed, code, _ = evaluate_race_card_quality('R1', [r1, r2])
        self.assertFalse(passed)
        self.assertEqual(code, QualityReasonCode.RACE_PROBABILITY_SUM_ANOMALY)

    def test_bootstrap_confidence_intervals(self):
        """ブートストラップ95%信頼区間の正常算出"""
        y_true = [1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 1]
        y_prob = [0.8, 0.2, 0.1, 0.7, 0.3, 0.6, 0.2, 0.1, 0.9, 0.2, 0.3, 0.7]
        ci = compute_bootstrap_confidence_intervals(y_true, y_prob, n_bootstraps=50, seed=42)
        self.assertEqual(ci['status'], 'evaluated')
        self.assertEqual(len(ci['log_loss_ci95']), 2)
        self.assertTrue(ci['log_loss_ci95'][0] <= ci['log_loss_ci95'][1])

    def test_bootstrap_insufficient_samples_rejected(self):
        """サンプル数不足時は算出不可と判定"""
        ci = compute_bootstrap_confidence_intervals([1, 0], [0.8, 0.2])
        self.assertEqual(ci['status'], 'unevaluable_insufficient_samples')
        self.assertIsNone(ci['log_loss_ci95'])

    def test_run_phase16_pipeline_execution(self):
        """Phase 16 パイプラインが正常に実行され、レポートが出力されること"""
        rep_path = self.root / 'p16_report.json'
        log_path = self.root / 'p16_audit.jsonl'
        res = run_phase16_pipeline(seed=42, n_races=20, n_folds=2, output_report=rep_path, output_audit_log=log_path)

        self.assertTrue(rep_path.exists())
        self.assertTrue(log_path.exists())
        self.assertIn('quality_gate_audit', res)
        self.assertIn('robustness_diagnostics', res)
        self.assertIn('hash_chain_verification', res)
        self.assertIn('SYNTHETIC BENCHMARK EVALUATION ONLY', res['disclaimer'])

if __name__ == '__main__':
    unittest.main()
