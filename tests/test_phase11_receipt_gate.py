import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.phase11_receipt_gate import audit_receipts

class Phase11ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.manifest=self.root/'manifest.json'
        self.pred=self.root/'pred.csv'
        self.odds=self.root/'odds.csv'
        self.feature=self.root/'feature.receipt.json'
        self.odds_receipt=self.root/'odds.receipt.json'
        self.pred.write_text('prediction')
        self.odds.write_bytes(b'odds')
        import hashlib
        self.odds_hash=hashlib.sha256(b'odds').hexdigest()
        self.feat_hash=hashlib.sha256(b'feature').hexdigest()
        self.manifest.write_text(json.dumps(dict(
            race_id='202610250101',race_start_at='2026-10-25T15:00:00+09:00',
            prediction_at='2026-10-25T14:40:00+09:00',
            feature_cutoff_at='2026-10-25T14:30:00+09:00',
            feature_sha256=self.feat_hash,odds_sha256=self.odds_hash)))
        common=dict(race_id='202610250101',
            race_start_at='2026-10-25T15:00:00+09:00',
            observed_at='2026-10-25T14:20:00+09:00')
        self.feature.write_text(json.dumps(dict(common,snapshot_file='f.snapshot')))
        self.odds_receipt.write_text(json.dumps(dict(common,snapshot_file='o.snapshot')))
        (self.root/'o.snapshot').write_bytes(b'odds')

    def _run(self):
        with patch('scripts.phase11_receipt_gate.verify_snapshot',
            side_effect=[{'sha256':self.feat_hash},{'sha256':self.odds_hash}]),patch(
                'scripts.phase11_receipt_gate.audit',return_value={'horses':1}):
            return audit_receipts(self.pred,self.odds,self.manifest,self.feature,self.odds_receipt)

    def test_consistent_receipts(self):
        self.assertEqual(self._run()['status'],'local-receipts-consistent')

    def test_manifest_hash_mismatch(self):
        obj=json.loads(self.manifest.read_text())
        obj['odds_sha256']='0'*64
        self.manifest.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):
            self._run()

    def test_odds_payload_mismatch(self):
        self.odds.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self._run()

    def test_late_feature_receipt_rejected(self):
        obj=json.loads(self.feature.read_text())
        obj['observed_at']='2026-10-25T14:35:00+09:00'
        self.feature.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):
            self._run()

    def test_real_file_end_to_end_receipt_audit(self):
        """モックなしの実ファイル・実スナップショットを用いたPhase 10 & Phase 9統合監査"""
        import csv
        from scripts.phase10_snapshot_attestation import capture_snapshot

        archive_dir = self.root / 'archive'
        archive_dir.mkdir(exist_ok=True)

        # 1. 実CSV作成
        real_feat = self.root / 'real_feat.csv'
        real_feat.write_text('race_id,horse_number\n202610250101,1\n', encoding='utf-8')

        real_odds = self.root / 'real_odds.csv'
        with real_odds.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['race_id','horse_number','odds_value','observed_at','fetched_at','race_start_at','source'])
            w.writerow(['202610250101', 1, '3.0', '2026-10-24T14:10:00+09:00', '2026-10-24T14:11:00+09:00', '2026-10-25T15:00:00+09:00', 'licensed-feed'])

        real_pred = self.root / 'real_pred.csv'
        with real_pred.open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=['race_id','horse_number','prediction_at','cutoff_at','win_probability','model_id','source'])
            w.writeheader()
            w.writerow(dict(race_id='202610250101', horse_number=1,
                            prediction_at='2026-10-24T14:30:00+09:00',
                            cutoff_at='2026-10-24T14:40:00+09:00', win_probability=0.55,
                            model_id='win-v2-test', source='licensed-feed'))

        # 2. Phase 10 スナップショット保存 (feat: 14:00, odds: 14:10)
        obs_feat = dt.datetime(2026, 10, 24, 5, 0, tzinfo=dt.timezone.utc)
        obs_odds = dt.datetime(2026, 10, 24, 5, 10, tzinfo=dt.timezone.utc)
        f_rec = capture_snapshot(real_feat, archive_dir, race_id='202610250101',
                                 race_start_at='2026-10-25T15:00:00+09:00', source_name='licensed-feed',
                                 observed_at=obs_feat)
        f_rec_path = archive_dir / (f_rec['snapshot_file'].replace('.snapshot', '.receipt.json'))

        o_rec = capture_snapshot(real_odds, archive_dir, race_id='202610250101',
                                 race_start_at='2026-10-25T15:00:00+09:00', source_name='licensed-feed',
                                 observed_at=obs_odds)
        o_rec_path = archive_dir / (o_rec['snapshot_file'].replace('.snapshot', '.receipt.json'))

        # 3. マニフェスト作成 (feat_cutoff: 14:05, pred_at: 14:30)
        real_manifest = self.root / 'real_manifest.json'
        real_manifest.write_text(json.dumps({
            'race_id': '202610250101',
            'race_start_at': '2026-10-25T15:00:00+09:00',
            'feature_cutoff_at': '2026-10-24T14:05:00+09:00',
            'prediction_at': '2026-10-24T14:30:00+09:00',
            'source': 'licensed-feed',
            'feature_sha256': f_rec['sha256'],
            'odds_sha256': o_rec['sha256'],
            'model_sha256': 'a' * 64
        }), encoding='utf-8')

        # 4. audit_receipts の実行（モックなし）
        res = audit_receipts(real_pred, real_odds, real_manifest, f_rec_path, o_rec_path,
                             now=dt.datetime(2026, 10, 24, 5, 40, tzinfo=dt.timezone.utc))
        self.assertEqual(res['status'], 'local-receipts-consistent')
        self.assertEqual(res['horses'], 1)

        # 5. オッズ改変時の拒絶（モックなし）
        tampered_odds = self.root / 'tampered.csv'
        tampered_odds.write_bytes(real_odds.read_bytes() + b'TAMPER')
        with self.assertRaises(ValueError):
            audit_receipts(real_pred, tampered_odds, real_manifest, f_rec_path, o_rec_path,
                           now=dt.datetime(2026, 10, 24, 5, 40, tzinfo=dt.timezone.utc))

if __name__=='__main__':
    unittest.main()
