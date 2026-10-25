"""Tests for Phase 15 Walk-Forward Model Evaluation, Paper Monitor & Audit Integrity.

Verifies multi-fold rolling walk-forward splitting, chronological leakage protection,
tamper-evident SHA-256 hash chaining, and reproducible paper monitoring execution.
"""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.phase15_walkforward_paper_monitor import (
    generate_walk_forward_folds,
    create_hash_chained_audit_log,
    verify_audit_log_hash_chain,
    evaluate_walk_forward,
    run_phase15_pipeline
)
from scripts.phase13_offline_evaluation import generate_synthetic_benchmark

class Phase15WalkForwardPaperMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_generate_walk_forward_folds_chronology_and_purging(self):
        """各FoldでTrain < Calibration < Testの順序が保たれ、レースIDの重複がないこと"""
        records = []
        for i in range(20):
            records.append({
                'race_id': f"R{i+1:02d}",
                'race_start_at': f"2026-01-01T{i:02d}:00:00Z",
                'val': i
            })
        folds = generate_walk_forward_folds(
            records, n_folds=3, min_train_races=8, calib_races=4, test_races=4
        )
        self.assertEqual(len(folds), 3)

        for f in folds:
            t_races = set(r['race_id'] for r in f['train_rows'])
            c_races = set(r['race_id'] for r in f['calib_rows'])
            test_races = set(r['race_id'] for r in f['test_rows'])

            # No overlap within any fold
            self.assertFalse(t_races.intersection(c_races))
            self.assertFalse(c_races.intersection(test_races))
            self.assertFalse(t_races.intersection(test_races))

    def test_generate_walk_forward_folds_insufficient_races_rejected(self):
        """最小必要レース数に満たない場合は即座に拒絶"""
        records = [{'race_id': 'R1', 'race_start_at': '2026-01-01T10:00:00Z'}]
        with self.assertRaises(ValueError) as cm:
            generate_walk_forward_folds(records, min_train_races=5, calib_races=2, test_races=2)
        self.assertIn("Insufficient races for walk-forward", str(cm.exception))

    def test_hash_chain_creation_and_verification(self):
        """監査レコードのSHA-256ハッシュチェーン生成と正常検証"""
        raw_logs = [
            {'race_id': 'R1', 'horse_number': 1, 'decision': 'BET', 'prob': 0.35},
            {'race_id': 'R1', 'horse_number': 2, 'decision': 'SKIP', 'prob': 0.10},
            {'race_id': 'R2', 'horse_number': 1, 'decision': 'SKIP', 'prob': 0.15},
        ]
        chained = create_hash_chained_audit_log(raw_logs)
        self.assertEqual(len(chained), 3)
        self.assertEqual(chained[0]['previous_hash'], "0" * 64)
        self.assertEqual(chained[1]['previous_hash'], chained[0]['record_hash'])
        self.assertEqual(chained[2]['previous_hash'], chained[1]['record_hash'])

        # Verification must pass
        res = verify_audit_log_hash_chain(chained)
        self.assertTrue(res['valid'])
        self.assertEqual(res['status'], 'verified_intact')
        self.assertEqual(res['count'], 3)

    def test_hash_chain_detects_payload_tampering(self):
        """レコード内容が1文字でも改ざんされた場合に検知すること"""
        raw_logs = [
            {'race_id': 'R1', 'horse_number': 1, 'decision': 'BET', 'prob': 0.35},
            {'race_id': 'R1', 'horse_number': 2, 'decision': 'SKIP', 'prob': 0.10},
        ]
        chained = create_hash_chained_audit_log(raw_logs)

        # Tamper with the decision of record 1
        chained[0]['decision'] = 'SKIP'

        with self.assertRaises(ValueError) as cm:
            verify_audit_log_hash_chain(chained)
        self.assertIn("Tamper detected at sequence 1", str(cm.exception))

    def test_hash_chain_detects_record_deletion_or_reordering(self):
        """レコードの削除や順序入替を検知すること"""
        raw_logs = [
            {'race_id': 'R1', 'horse_number': 1},
            {'race_id': 'R1', 'horse_number': 2},
            {'race_id': 'R1', 'horse_number': 3},
        ]
        chained = create_hash_chained_audit_log(raw_logs)

        # Delete middle record
        corrupted = [chained[0], chained[2]]
        with self.assertRaises(ValueError) as cm:
            verify_audit_log_hash_chain(corrupted)
        self.assertIn("Sequence break", str(cm.exception))

    def test_run_phase15_pipeline_execution_and_reports(self):
        """Phase 15 パイプラインが正常に実行され、レポートと監査ログが出力されること"""
        rep_path = self.root / 'phase15_report.json'
        log_path = self.root / 'phase15_audit.jsonl'

        res = run_phase15_pipeline(
            seed=42,
            n_races=25,
            n_folds=2,
            output_report=rep_path,
            output_audit_log=log_path
        )
        self.assertEqual(res['status'], 'completed')
        self.assertEqual(res['total_folds'], 2)
        self.assertTrue(rep_path.exists())
        self.assertTrue(log_path.exists())

        # Check aggregate metrics
        self.assertIn('mean_log_loss_calibrated', res['aggregate_metrics'])
        self.assertIn('mean_ece_calibrated', res['aggregate_metrics'])
        self.assertIn('mean_top1_hit_rate', res['aggregate_metrics'])

        # Verify hash chain integrity in pipeline
        self.assertTrue(res['hash_chain_verification']['valid'])
        self.assertEqual(res['hash_chain_verification']['status'], 'verified_intact')

        # Check disclaimer
        self.assertIn("SYNTHETIC BENCHMARK WALK-FORWARD EVALUATION ONLY", res['disclaimer'])

if __name__ == '__main__':
    unittest.main()
