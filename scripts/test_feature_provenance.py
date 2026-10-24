import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from src.feature_provenance import snapshot_features,verify_snapshot

class ProvenanceTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
  self.src=Path(self.t.name)/'source.csv';self.dst=Path(self.t.name)/'snapshot.csv'
  self.src.write_text('race_id,horse_number,f\nR,1,2\nR,2,3\n')
  self.now=dt.datetime(2026,10,24,7,tzinfo=dt.timezone.utc)
 def make(self): return snapshot_features(self.src,self.dst,'R',now=self.now)
 def test_valid(self):
  self.make();self.assertEqual(verify_snapshot(self.dst,'R','2026-10-24T07:01:00+00:00')['horse_count'],2)
 def test_tamper(self):
  self.make();self.dst.write_text('race_id,horse_number,f\nR,1,99\n')
  with self.assertRaises(ValueError):verify_snapshot(self.dst,'R','2026-10-24T07:01:00+00:00')
 def test_future(self):
  self.make()
  with self.assertRaises(ValueError):verify_snapshot(self.dst,'R','2026-10-24T06:59:00+00:00')
 def test_wrong_race(self):
  self.make()
  with self.assertRaises(ValueError):verify_snapshot(self.dst,'X','2026-10-24T07:01:00+00:00')
 def test_no_overwrite(self):
  self.make()
  with self.assertRaises(FileExistsError):self.make()
 def test_mixed_race(self):
  self.src.write_text('race_id,horse_number\nR,1\nS,2\n')
  with self.assertRaises(ValueError):self.make()
if __name__=='__main__':unittest.main()
