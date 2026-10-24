import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.phase9_pre_race_gate import audit

class Phase9GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.pred = base / 'pred.csv'
        self.odds = base / 'odds.csv'
        self.manifest = base / 'manifest.json'
        self.race = '202610250101'
        self.start = '2026-10-25T15:00:00+09:00'
        self.pred_at = '2026-10-25T14:40:00+09:00'
        self.now = datetime(2026,10,25,6,0,tzinfo=timezone.utc)
        self.meta = dict(race_id=self.race,race_start_at=self.start,
            feature_cutoff_at='2026-10-25T14:30:00+09:00',
            prediction_at=self.pred_at,source='test-snapshot',
            feature_sha256='a'*64,model_sha256='b'*64)
        self._write()

    def _write(self):
        self.manifest.write_text(json.dumps(self.meta),encoding='utf-8')
        with self.pred.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['race_id','horse_number','prediction_at',
                'cutoff_at','win_probability','model_id','source'])
            w.writeheader()
            w.writerow(dict(race_id=self.race,horse_number=1,prediction_at=self.pred_at,
                cutoff_at='2026-10-25T14:45:00+09:00',win_probability=0.6,
                model_id='win-test',source='test-snapshot'))
        with self.odds.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['race_id','horse_number','odds_value',
                'observed_at','fetched_at','race_start_at','source'])
            w.writeheader()
            w.writerow(dict(race_id=self.race,horse_number=1,odds_value=2.5,
                observed_at='2026-10-25T14:35:00+09:00',
                fetched_at='2026-10-25T14:36:00+09:00',
                race_start_at=self.start,source='test-odds'))

    def test_consistent_metadata(self):
        self.assertEqual(audit(self.pred,self.odds,self.manifest,now=self.now)['horses'],1)

    def test_future_prediction_rejected(self):
        with self.assertRaises(ValueError):
            audit(self.pred,self.odds,self.manifest,
                now=datetime(2026,10,25,0,0,tzinfo=timezone.utc))

    def test_wrong_manifest_race_rejected(self):
        self.meta['race_id']='different'
        self._write()
        with self.assertRaises(ValueError):
            audit(self.pred,self.odds,self.manifest,now=self.now)

    def test_invalid_hash_rejected(self):
        self.meta['model_sha256']='unknown'
        self._write()
        with self.assertRaises(ValueError):
            audit(self.pred,self.odds,self.manifest,now=self.now)

    def test_post_race_prediction_rejected(self):
        """発走時刻以降の予測は確実に拒否"""
        self.meta['prediction_at'] = '2026-10-25T15:05:00+09:00'
        self._write()
        with self.assertRaises(ValueError):
            audit(self.pred, self.odds, self.manifest, now=datetime(2026,10,25,7,0,tzinfo=timezone.utc))

    def test_post_race_cutoff_rejected(self):
        """締切時刻が発走時刻以降の場合は確実に拒否"""
        with self.pred.open('w', newline='') as f:
            import csv
            w = csv.DictWriter(f, fieldnames=['race_id','horse_number','prediction_at',
                'cutoff_at','win_probability','model_id','source'])
            w.writeheader()
            w.writerow(dict(race_id=self.race, horse_number=1, prediction_at=self.pred_at,
                cutoff_at='2026-10-25T15:01:00+09:00', win_probability=0.6,
                model_id='win-test', source='test-snapshot'))
        with self.assertRaises(ValueError):
            audit(self.pred, self.odds, self.manifest, now=self.now)

    def test_future_odds_fetch_rejected(self):
        """オッズ取得時刻が現在時刻より未来の場合は確実に拒否"""
        with self.odds.open('w', newline='') as f:
            import csv
            w = csv.DictWriter(f, fieldnames=['race_id','horse_number','odds_value',
                'observed_at','fetched_at','race_start_at','source'])
            w.writeheader()
            w.writerow(dict(race_id=self.race, horse_number=1, odds_value=2.5,
                observed_at='2026-10-25T14:35:00+09:00',
                fetched_at='2026-10-25T14:45:00+09:00',
                race_start_at=self.start, source='test-odds'))
        # now は 14:40 (UTC 05:40) なので fetched_at 14:45 は未来
        with self.assertRaises(ValueError):
            audit(self.pred, self.odds, self.manifest, now=datetime(2026,10,25,5,40,tzinfo=timezone.utc))

    def test_card_size_mismatch_rejected(self):
        """予測件数とオッズ件数が不一致の場合は確実に拒否"""
        with self.pred.open('w', newline='') as f:
            import csv
            w = csv.DictWriter(f, fieldnames=['race_id','horse_number','prediction_at',
                'cutoff_at','win_probability','model_id','source'])
            w.writeheader()
            w.writerow(dict(race_id=self.race, horse_number=1, prediction_at=self.pred_at,
                cutoff_at='2026-10-25T14:45:00+09:00', win_probability=0.6,
                model_id='win-test', source='test-snapshot'))
            w.writerow(dict(race_id=self.race, horse_number=2, prediction_at=self.pred_at,
                cutoff_at='2026-10-25T14:45:00+09:00', win_probability=0.4,
                model_id='win-test', source='test-snapshot'))
        with self.assertRaises(ValueError):
            audit(self.pred, self.odds, self.manifest, now=self.now)

if __name__ == '__main__':
    unittest.main()
