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
if __name__=='__main__':
    unittest.main()
