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
        self.source.write_text('race_id,horse_number\\n123,1\\n')
        self.kw=dict(race_id='202610250101',race_start_at='2026-10-25T15:00:00+09:00',
                     source_name='licensed-local-import',
                     observed_at=dt.datetime(2026,10,25,5,0,tzinfo=dt.timezone.utc))
    def test_capture_and_verify(self):
        receipt=capture_snapshot(self.source,self.root,**self.kw)
        path=next(self.root.glob('*.receipt.json'))
        self.assertEqual(verify_snapshot(path)['status'],'hash-matched')
        self.assertEqual(receipt['provenance_level'],'local_file_observation_only')
    def test_tampering_detected(self):
        capture_snapshot(self.source,self.root,**self.kw)
        path=next(self.root.glob('*.receipt.json'))
        name=json.loads(path.read_text())['snapshot_file']
        (self.root/name).write_text('tampered')
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
if __name__=='__main__':
    unittest.main()
