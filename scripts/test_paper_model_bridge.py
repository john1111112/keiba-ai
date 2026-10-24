import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
import pandas as pd
from src.paper_model_bridge import prepare_predictions

T='2026-10-24T10:00:00+09:00'
C='2026-10-24T10:10:00+09:00'
Q=pd.DataFrame([dict(horse_number=n,odds_value=2.0+n,observed_at='2026-10-24T09:55:00+09:00',fetched_at='2026-10-24T09:56:00+09:00',source='fixture',data_status='pre_10m') for n in (1,2)])
class BridgeTests(unittest.TestCase):
 def setUp(self):
  self.df=pd.DataFrame(dict(race_id=['R','R'],horse_number=[1,2],feat=[.1,.2]))
  self.model=Mock(feature_cols=['feat']); self.model.model.predict_proba.return_value=np.array([[.7,.3],[.4,.6]])
 def run_bridge(self, **kw):
  with patch('src.paper_model_bridge.get_latest_odds_as_of',return_value=kw.pop('quotes',Q)):
   return prepare_predictions(kw.pop('features',self.df),'R',T,C,'unused',self.model,'verified-model',generated_at=kw.pop('generated_at',T))
 def test_good(self):
  rows=self.run_bridge(); self.assertEqual(len(rows),2); self.assertAlmostEqual(sum(r['win_probability'] for r in rows),1)
 def test_missing_odds(self):
  with self.assertRaises(ValueError): self.run_bridge(quotes=Q.iloc[:1])
 def test_missing_feature(self):
  with self.assertRaises(ValueError): self.run_bridge(features=self.df.drop(columns='feat'))
 def test_duplicate_horse(self):
  with self.assertRaises(ValueError): self.run_bridge(features=self.df.assign(horse_number=[1,1]))
 def test_no_backdating(self):
  with self.assertRaises(ValueError): self.run_bridge(generated_at=C)
 def test_bad_model_output(self):
  self.model.model.predict_proba.return_value=np.array([[.7,np.nan],[.4,.6]])
  with self.assertRaises(ValueError): self.run_bridge()
if __name__=='__main__': unittest.main()
