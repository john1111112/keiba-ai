"""Tests for Phase 12 Licensed Pre-Race Feed Adapter.

Verifies contract schema validation, fail-closed license enforcement,
local timestamp attribution, cryptographic integrity, and integration
with Phase 10 snapshot attestation & Phase 11 receipt gate.
"""
import csv
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from scripts.phase12_feed_adapter import ingest_feed_fixture
from scripts.phase10_snapshot_attestation import capture_snapshot, verify_snapshot
from scripts.phase11_receipt_gate import audit_receipts

class Phase12FeedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.race_id = '202610240101'
        self.race_start_at = '2026-10-24T15:30:00+09:00'
        self.provider_id = 'licensed-feed-test'

        # Reference observation time: past UTC relative to current runner clock
        self.obs_time = dt.datetime(2026, 10, 24, 5, 0, tzinfo=dt.timezone.utc) # 14:00 JST (before 15:30 start)

        # Create valid fixture CSV
        self.valid_fixture = self.root / 'valid_feed.csv'
        with self.valid_fixture.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['race_id', 'horse_number', 'odds_value', 'upstream_published_at'])
            w.writerow([self.race_id, 1, '2.5', '2026-10-24T13:58:00+09:00'])
            w.writerow([self.race_id, 2, '5.0', '2026-10-24T13:58:00+09:00'])
            w.writerow([self.race_id, 3, '12.4', '2026-10-24T13:58:00+09:00'])

    def tearDown(self):
        self.tmp.cleanup()

    def test_successful_ingest_with_license(self):
        """正常系: verified_licensed で正しくパースされ、契約メタデータが返ること"""
        res = ingest_feed_fixture(
            self.valid_fixture,
            race_id=self.race_id,
            race_start_at=self.race_start_at,
            provider_id=self.provider_id,
            license_status='verified_licensed',
            now_utc=self.obs_time
        )
        self.assertEqual(res['status'], 'accepted')
        self.assertEqual(res['horse_count'], 3)
        self.assertEqual(res['race_id'], self.race_id)
        self.assertEqual(res['provider_id'], self.provider_id)
        self.assertEqual(res['license_status'], 'verified_licensed')
        self.assertTrue(len(res['raw_sha256']) == 64)
        self.assertEqual(res['observed_at_utc'], self.obs_time.isoformat())
        self.assertIsNotNone(res['upstream_published_at_utc'])

    def test_unverified_license_fails_closed(self):
        """異常系: license_status が verified_licensed でない場合は即座に拒絶"""
        for bad_status in ['unverified', 'unlicensed', '', 'pending', 'third_party_web']:
            with self.subTest(bad_status=bad_status):
                with self.assertRaises(ValueError) as cm:
                    ingest_feed_fixture(
                        self.valid_fixture,
                        race_id=self.race_id,
                        race_start_at=self.race_start_at,
                        provider_id=self.provider_id,
                        license_status=bad_status,
                        now_utc=self.obs_time
                    )
                self.assertIn('unverified or missing license', str(cm.exception))

    def test_network_remote_url_prohibited(self):
        """異常系: HTTP/HTTPS リモート取得は Phase 12 で禁止"""
        for url in ['http://api.keiba.example.com/odds.csv', 'https://example.com/feed']:
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as cm:
                    ingest_feed_fixture(
                        url,
                        race_id=self.race_id,
                        race_start_at=self.race_start_at,
                        provider_id=self.provider_id,
                        license_status='verified_licensed',
                        now_utc=self.obs_time
                    )
                self.assertIn('remote network fetch is prohibited', str(cm.exception))

    def test_post_start_observation_rejected(self):
        """異常系: 発走後データ観測は即座に拒絶"""
        post_start_time = dt.datetime(2026, 10, 24, 6, 35, tzinfo=dt.timezone.utc) # 15:35 JST (Start is 15:30)
        with self.assertRaises(ValueError) as cm:
            ingest_feed_fixture(
                self.valid_fixture,
                race_id=self.race_id,
                race_start_at=self.race_start_at,
                provider_id=self.provider_id,
                license_status='verified_licensed',
                now_utc=post_start_time
            )
        self.assertIn('post-start', str(cm.exception))

    def test_future_observation_rejected(self):
        """異常系: 現在時刻から未来の日時を指定された場合は拒絶"""
        far_future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5)
        with self.assertRaises(ValueError) as cm:
            ingest_feed_fixture(
                self.valid_fixture,
                race_id=self.race_id,
                race_start_at='2030-01-01T15:00:00+09:00',
                provider_id=self.provider_id,
                license_status='verified_licensed',
                now_utc=far_future
            )
        self.assertIn('future observation timestamp rejected', str(cm.exception))

    def test_race_id_mismatch_rejected(self):
        """異常系: CSV 行内の race_id が指定と異なる場合は拒絶"""
        bad_fixture = self.root / 'bad_race.csv'
        bad_fixture.write_text(
            f"race_id,horse_number,odds_value\n{self.race_id},1,3.0\n999999999999,2,4.0\n",
            encoding='utf-8'
        )
        with self.assertRaises(ValueError) as cm:
            ingest_feed_fixture(
                bad_fixture,
                race_id=self.race_id,
                race_start_at=self.race_start_at,
                provider_id=self.provider_id,
                license_status='verified_licensed',
                now_utc=self.obs_time
            )
        self.assertIn('race_id mismatch', str(cm.exception))

    def test_duplicate_horse_number_rejected(self):
        """異常系: 同一馬番の重複は拒絶"""
        dup_fixture = self.root / 'dup_horse.csv'
        dup_fixture.write_text(
            f"race_id,horse_number,odds_value\n{self.race_id},1,3.0\n{self.race_id},1,4.0\n",
            encoding='utf-8'
        )
        with self.assertRaises(ValueError) as cm:
            ingest_feed_fixture(
                dup_fixture,
                race_id=self.race_id,
                race_start_at=self.race_start_at,
                provider_id=self.provider_id,
                license_status='verified_licensed',
                now_utc=self.obs_time
            )
        self.assertIn('duplicate horse_number', str(cm.exception))

    def test_invalid_odds_value_rejected(self):
        """異常系: 1.0未満、負数、NaN、空文字などの無効オッズは拒絶"""
        for bad_val in ['0.9', '-1.0', 'nan', 'abc', '']:
            with self.subTest(bad_val=bad_val):
                bad_fixture = self.root / 'bad_odds.csv'
                bad_fixture.write_text(
                    f"race_id,horse_number,odds_value\n{self.race_id},1,{bad_val}\n",
                    encoding='utf-8'
                )
                with self.assertRaises(ValueError):
                    ingest_feed_fixture(
                        bad_fixture,
                        race_id=self.race_id,
                        race_start_at=self.race_start_at,
                        provider_id=self.provider_id,
                        license_status='verified_licensed',
                        now_utc=self.obs_time
                    )

    def test_missing_required_column_rejected(self):
        """異常系: 必須カラム欠損は拒絶"""
        bad_fixture = self.root / 'missing_col.csv'
        bad_fixture.write_text(
            f"race_id,odds_value\n{self.race_id},3.5\n",
            encoding='utf-8'
        )
        with self.assertRaises(ValueError) as cm:
            ingest_feed_fixture(
                bad_fixture,
                race_id=self.race_id,
                race_start_at=self.race_start_at,
                provider_id=self.provider_id,
                license_status='verified_licensed',
                now_utc=self.obs_time
            )
        self.assertIn('missing required columns', str(cm.exception))

    def test_integration_with_phase10_and_phase11_gates(self):
        """結合テスト: Phase 10 snapshot保存 -> Phase 11 receipt auditまでの一連連携"""
        archive_dir = self.root / 'archive'
        normalized_odds = self.root / 'normalized_odds.csv'

        res = ingest_feed_fixture(
            self.valid_fixture,
            race_id=self.race_id,
            race_start_at=self.race_start_at,
            provider_id=self.provider_id,
            license_status='verified_licensed',
            archive_dir=archive_dir,
            normalized_output_path=normalized_odds,
            now_utc=self.obs_time
        )
        self.assertIsNotNone(res['snapshot_receipt'])
        self.assertIsNotNone(res['snapshot_receipt_path'])
        self.assertTrue(Path(res['normalized_odds_path']).exists())

        # Phase 10 verify_snapshot でスナップショットが正しく検証できること
        verify_res = verify_snapshot(res['snapshot_receipt_path'])
        self.assertEqual(verify_res['status'], 'hash-matched')
        self.assertEqual(verify_res['sha256'], res['snapshot_receipt']['sha256'])

        # 次に Phase 9 / 11 の入力要件を満たす特徴量・予測CSV・マニフェストを作成し、Phase 11 audit_receipts を通過させる
        # 1. 特徴量 (観測: 13:30 JST = 04:30 UTC)
        feat_csv = self.root / 'feat.csv'
        feat_csv.write_text(f"race_id,horse_number\n{self.race_id},1\n{self.race_id},2\n{self.race_id},3\n", encoding='utf-8')
        feat_obs = dt.datetime(2026, 10, 24, 4, 30, tzinfo=dt.timezone.utc)
        f_rec = capture_snapshot(
            feat_csv, archive_dir, race_id=self.race_id,
            race_start_at=self.race_start_at, source_name='feature-pipeline',
            observed_at=feat_obs
        )
        f_rec_path = archive_dir / f_rec['snapshot_file'].replace('.snapshot', '.receipt.json')

        # 2. 予測 CSV (observed: 14:00 <= pred: 14:10 <= cutoff: 14:20 < start: 15:30)
        pred_csv = self.root / 'pred.csv'
        with pred_csv.open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=[
                'race_id', 'horse_number', 'prediction_at', 'cutoff_at',
                'win_probability', 'model_id', 'source'
            ])
            w.writeheader()
            for h in [1, 2, 3]:
                w.writerow({
                    'race_id': self.race_id,
                    'horse_number': h,
                    'prediction_at': '2026-10-24T14:10:00+09:00',
                    'cutoff_at': '2026-10-24T14:20:00+09:00',
                    'win_probability': 0.33,
                    'model_id': 'model-v1',
                    'source': self.provider_id
                })

        # 3. マニフェスト (feat_cutoff: 14:05 <= pred_at: 14:10, odds_obs: 14:00 <= pred_at)
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({
            'race_id': self.race_id,
            'race_start_at': self.race_start_at,
            'feature_cutoff_at': '2026-10-24T14:05:00+09:00',
            'prediction_at': '2026-10-24T14:10:00+09:00',
            'source': self.provider_id,
            'feature_sha256': f_rec['sha256'],
            'odds_sha256': res['snapshot_receipt']['sha256'],
            'model_sha256': 'b' * 64
        }), encoding='utf-8')

        # 4. Phase 11 audit_receipts を実行 (now: 14:20 JST = 05:20 UTC)
        snap_file = archive_dir / res['snapshot_receipt']['snapshot_file']
        audit_res = audit_receipts(
            pred_csv,
            snap_file,
            manifest,
            f_rec_path,
            res['snapshot_receipt_path'],
            now=dt.datetime(2026, 10, 24, 5, 20, tzinfo=dt.timezone.utc)
        )
        self.assertEqual(audit_res['status'], 'local-receipts-consistent')
        self.assertEqual(audit_res['horses'], 3)
        self.assertEqual(audit_res['odds_sha256'], res['snapshot_receipt']['sha256'])

if __name__ == '__main__':
    unittest.main()
