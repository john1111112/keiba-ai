import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.training_evidence import filter_training_evidence
from src.model import KeibaAIModel

class Phase4ETests(unittest.TestCase):
    def setUp(self):
        self.f = pd.DataFrame([{'race_id':'R1','horse_number':i,'finish_order':fo,'time_str':tm} for i,fo,tm in [(1,1,'1:30.0'),(2,15,'-'),(3,0,''),(4,3,'1:32.0')]])
    def evidence(self, horse=2, status='STARTED'):
        return pd.DataFrame([{'race_id':'R1','horse_number':horse,'start_status':status,'verified_source':'official','source_reference':'record','verified_at':'2026-01-01T00:00:00+09:00','reviewer':'human'}])
    def test_unresolved_never_labeled_negative(self):
        out,stats=filter_training_evidence(self.f,self.evidence())
        self.assertEqual(out.horse_number.tolist(),[1,4]); self.assertEqual(stats['verified_started_without_finish_excluded'],1)
    def test_conflicting_nonstarter_rejected(self):
        with self.assertRaises(ValueError): filter_training_evidence(self.f,self.evidence(1,'SCRATCHED'))
    def test_duplicate_evidence_rejected(self):
        v=self.evidence()
        with self.assertRaises(ValueError): filter_training_evidence(self.f,pd.concat([v,v]))
    def test_unverified_remains_excluded(self):
        out,_=filter_training_evidence(self.f)
        self.assertEqual(len(out),2)
    def test_unknown_status_rejected(self):
        with self.assertRaises(ValueError): filter_training_evidence(self.f,self.evidence(status='GUESS'))
    def test_unknown_horse_rejected(self):
        with self.assertRaises(ValueError): filter_training_evidence(self.f,self.evidence(horse=99))
    def test_model_guard_does_not_train(self):
        with self.assertRaisesRegex(ValueError,'不足'):
            KeibaAIModel.validate_and_filter_training_data(self.f, 'win', verified_evidence=self.evidence())

if __name__=='__main__': unittest.main()
