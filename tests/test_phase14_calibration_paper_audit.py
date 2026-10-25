"""Tests for Phase 14 Probability Calibration & Paper Decision Audit.

Verifies strict 3-way time-series splitting, probability integrity,
Platt scaling calibration, ECE calculation, and reproducible paper trade auditing.
"""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.phase14_calibration_paper_audit import (
    purged_three_way_split,
    audit_probability_integrity,
    PlattCalibrator,
    calibrate_race_probabilities,
    compute_ece,
    audit_paper_decisions,
    run_phase14_pipeline
)

class Phase14CalibrationPaperAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_purged_three_way_split_chronology_and_non_overlap(self):
        """Train < Calibration < Test の厳格な時系列分離とレース跨ぎの排除"""
        records = [
            {'race_id': 'R1', 'race_start_at': '2026-01-01T10:00:00Z'},
            {'race_id': 'R2', 'race_start_at': '2026-01-01T11:00:00Z'},
            {'race_id': 'R3', 'race_start_at': '2026-01-01T12:00:00Z'},
            {'race_id': 'R4', 'race_start_at': '2026-01-01T13:00:00Z'},
            {'race_id': 'R5', 'race_start_at': '2026-01-01T14:00:00Z'},
        ]
        train, calib, test = purged_three_way_split(records, train_ratio=0.4, calib_ratio=0.4)
        t_races = set(r['race_id'] for r in train)
        c_races = set(r['race_id'] for r in calib)
        test_races = set(r['race_id'] for r in test)

        self.assertEqual(t_races, {'R1', 'R2'})
        self.assertEqual(c_races, {'R3', 'R4'})
        self.assertEqual(test_races, {'R5'})

        # No overlapping race IDs
        self.assertFalse(t_races.intersection(c_races))
        self.assertFalse(c_races.intersection(test_races))
        self.assertFalse(t_races.intersection(test_races))

    def test_purged_three_way_split_requires_min_races(self):
        """3レース未満の場合は例外を発生"""
        records = [
            {'race_id': 'R1', 'race_start_at': '2026-01-01T10:00:00Z'},
            {'race_id': 'R2', 'race_start_at': '2026-01-01T11:00:00Z'},
        ]
        with self.assertRaises(ValueError) as cm:
            purged_three_way_split(records)
        self.assertIn("At least 3 distinct races required", str(cm.exception))

    def test_audit_probability_integrity_catches_anomalies(self):
        """確率異常値、欠損、総和不整合を厳格に検知"""
        # 1. Invalid range (> 1.0)
        with self.assertRaises(ValueError):
            audit_probability_integrity([{'race_id': 'R1', 'prob_raw': 1.5}])

        # 2. NaN
        with self.assertRaises(ValueError):
            audit_probability_integrity([{'race_id': 'R1', 'prob_raw': float('nan')}])

        # 3. Sum anomaly (sum is far from 1.0)
        bad_sum = [
            {'race_id': 'R1', 'prob_raw': 0.1},
            {'race_id': 'R1', 'prob_raw': 0.1}
        ]
        with self.assertRaises(ValueError) as cm:
            audit_probability_integrity(bad_sum)
        self.assertIn("probability sum anomaly", str(cm.exception))

    def test_platt_calibrator_and_normalization(self):
        """Platt校正器の適合およびレース単位正規化の検証"""
        calibrator = PlattCalibrator(iterations=50)
        # Dummy calibration data
        calibrator.fit([1, 0, 0, 1], [0.7, 0.2, 0.3, 0.6])

        test_data = [
            {'race_id': 'R1', 'horse_number': 1, 'prob_raw': 0.6},
            {'race_id': 'R1', 'horse_number': 2, 'prob_raw': 0.4},
        ]
        calibrated = calibrate_race_probabilities(test_data, calibrator)
        self.assertEqual(len(calibrated), 2)
        total_p = sum(r['prob_calibrated'] for r in calibrated)
        self.assertAlmostEqual(total_p, 1.0, places=4)

    def test_compute_ece(self):
        """Expected Calibration Error (ECE) の算出検証"""
        y_true = [1, 0, 1, 0]
        y_prob = [0.9, 0.1, 0.8, 0.2]
        ece = compute_ece(y_true, y_prob, n_bins=5)
        self.assertIsNotNone(ece)
        self.assertTrue(0.0 <= ece <= 1.0)

    def test_audit_paper_decisions_ev_threshold_and_roi_gating(self):
        """意思決定監査ログの生成とROIゲーティングの検証"""
        records = [
            # High EV -> BET
            {
                'race_id': 'R1', 'horse_number': 1, 'prob_calibrated': 0.40,
                'odds_value': 3.5, 'prediction_at': '2026-01-01T11:45:00Z',
                'cutoff_at': '2026-01-01T11:50:00Z', 'race_start_at': '2026-01-01T12:00:00Z',
                'is_synthetic': True
            },
            # Low EV -> SKIP
            {
                'race_id': 'R1', 'horse_number': 2, 'prob_calibrated': 0.10,
                'odds_value': 2.0, 'prediction_at': '2026-01-01T11:45:00Z',
                'cutoff_at': '2026-01-01T11:50:00Z', 'race_start_at': '2026-01-01T12:00:00Z',
                'is_synthetic': True
            }
        ]
        res = audit_paper_decisions(records, ev_threshold=1.10, prob_threshold=0.10)
        self.assertEqual(res['total_bets'], 1)
        self.assertEqual(res['total_skips'], 1)
        self.assertEqual(res['audit_logs'][0]['decision'], 'BET')
        self.assertEqual(res['audit_logs'][1]['decision'], 'SKIP')

        # Synthetic data must result in unevaluable ROI
        self.assertEqual(res['roi_report']['roi_status'], 'unevaluable_unverified_data')
        self.assertIsNone(res['roi_report']['roi'])

    def test_run_phase14_pipeline_output(self):
        """Phase 14 パイプライン実行、レポート・JSONL監査ログ出力の検証"""
        report_path = self.root / 'report.json'
        audit_log_path = self.root / 'audit.jsonl'

        rep = run_phase14_pipeline(
            seed=42,
            output_report=report_path,
            output_audit_log=audit_log_path
        )
        self.assertTrue(report_path.exists())
        self.assertTrue(audit_log_path.exists())

        # Check disclaimer
        self.assertIn("SYNTHETIC BENCHMARK EVALUATION ONLY", rep['disclaimer'])
        # Check metrics existence
        self.assertIn('expected_calibration_error', rep['calibration_metrics'])
        self.assertIn('log_loss', rep['calibration_metrics'])
        self.assertIn('brier_score', rep['calibration_metrics'])

if __name__ == '__main__':
    unittest.main()
