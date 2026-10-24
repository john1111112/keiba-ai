import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from scripts.phase10_snapshot_attestation import capture_snapshot, verify_snapshot

class Phase10SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'odds.csv'
        self.source.write_text('race_id,horse_number\n123,1\n', encoding='utf-8')
        self.kw=dict(race_id='202610250101',race_start_at='2026-10-25T15:00:00+09:00',
                     source_name='licensed-local-import',
                     observed_at=dt.datetime(2026,10,24,5,0,tzinfo=dt.timezone.utc))

    def test_capture_and_verify(self):
        receipt=capture_snapshot(self.source,self.root,**self.kw)
        path=next(self.root.glob('*.receipt.json'))
        self.assertEqual(verify_snapshot(path)['status'],'hash-matched')
        self.assertEqual(receipt['provenance_level'],'local_file_observation_only')

    def test_tampering_detected(self):
        capture_snapshot(self.source,self.root,**self.kw)
        path=next(self.root.glob('*.receipt.json'))
        name=json.loads(path.read_text())['snapshot_file']
        (self.root/name).write_text('tampered', encoding='utf-8')
        with self.assertRaises(ValueError):
            verify_snapshot(path)

    def test_post_start_rejected(self):
        self.kw['observed_at']=dt.datetime(2026,10,25,7,0,tzinfo=dt.timezone.utc)
        with self.assertRaises(ValueError):
            capture_snapshot(self.source,self.root,**self.kw)

    def test_empty_rejected(self):
        self.source.write_bytes(b'')
        with self.assertRaises(ValueError):
            capture_snapshot(self.source,self.root,**self.kw)

    def test_integration_with_phase9_manifest_attestation(self):
        """Phase 10 で証明された SHA-256 を Phase 9 の manifest に連携し、ゲート整合性を検証"""
        import csv
        from scripts.phase9_pre_race_gate import audit

        # 1. 有効な発走前オッズと予測CSVを作成
        odds_file = self.root / 'pre_odds.csv'
        with odds_file.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['race_id','horse_number','odds_value','observed_at','fetched_at','race_start_at','source'])
            w.writerow([self.kw['race_id'], 1, '2.5', '2026-10-24T14:00:00+09:00', '2026-10-24T14:01:00+09:00', self.kw['race_start_at'], 'test-source'])

        pred_file = self.root / 'predictions.csv'
        with pred_file.open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=['race_id','horse_number','prediction_at','cutoff_at','win_probability','model_id','source'])
            w.writeheader()
            w.writerow(dict(race_id=self.kw['race_id'], horse_number=1,
                            prediction_at='2026-10-24T14:30:00+09:00',
                            cutoff_at='2026-10-24T14:40:00+09:00', win_probability=0.6,
                            model_id='win-v2-test', source='test-source'))

        feat_file = self.root / 'features.csv'
        feat_file.write_text('race_id,horse_number\n202610250101,1\n', encoding='utf-8')

        # 2. Phase 10 で特徴量スナップショットをアーカイブしてレシート取得
        feat_receipt = capture_snapshot(feat_file, self.root, race_id=self.kw['race_id'],
                                        race_start_at=self.kw['race_start_at'], source_name='test-source',
                                        observed_at=self.kw['observed_at'])
        receipt_path = self.root / (feat_receipt['snapshot_file'].replace('.snapshot', '.receipt.json'))
        self.assertEqual(verify_snapshot(receipt_path)['status'], 'hash-matched')

        # 3. Phase 10 レシートの SHA-256 を Phase 9 manifest に注入
        manifest_file = self.root / 'manifest.json'
        manifest = {
            'race_id': self.kw['race_id'],
            'race_start_at': self.kw['race_start_at'],
            'feature_cutoff_at': '2026-10-24T14:00:00+09:00',
            'prediction_at': '2026-10-24T14:30:00+09:00',
            'source': 'test-source',
            'feature_sha256': feat_receipt['sha256'],
            'model_sha256': 'd' * 64
        }
        manifest_file.write_text(json.dumps(manifest), encoding='utf-8')

        # 4. Phase 9 ゲート検証の実行
        res = audit(pred_file, odds_file, manifest_file, now=dt.datetime(2026, 10, 24, 5, 40, tzinfo=dt.timezone.utc))
        self.assertEqual(res['status'], 'metadata-consistent')
        self.assertEqual(res['feature_sha256'], feat_receipt['sha256'])
        self.assertEqual(res['horses'], 1)

if __name__=='__main__':
    unittest.main()
