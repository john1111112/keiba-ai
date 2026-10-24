import unittest
import pandas as pd
from src.race_quality import assess_races, select_reviewed_races
class RaceQualityTests(unittest.TestCase):
 def test_unresolved_and_missing_winner(self):
  d=pd.DataFrame({'race_id':['a','a','b','b'],'horse_number':[1,2,1,2],'finish_order':[1,15,2,3],'time_str':['1:20','','1:22','1:23']})
  q=assess_races(d).set_index('race_id')
  self.assertEqual(int(q.loc['a','unresolved_rows']),1)
  self.assertFalse(bool(q.loc['b','candidate_for_review']))
  self.assertFalse(q['verified_complete'].any())
 def test_review_gate(self):
  d=pd.DataFrame({'race_id':['a','b'],'x':[1,2]})
  r=pd.DataFrame({'race_id':['a','b'],'verified_complete':[True,False]})
  self.assertEqual(select_reviewed_races(d,r).race_id.tolist(),['a'])
 def test_no_implicit_approval(self):
  d=pd.DataFrame({'race_id':['a']})
  with self.assertRaises(ValueError):select_reviewed_races(d,pd.DataFrame({'race_id':['a']}))
if __name__=='__main__':unittest.main()
