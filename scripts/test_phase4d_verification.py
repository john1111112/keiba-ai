import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.runner_status_verification import review_template, validate_review, reconcile_review

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.audit = pd.DataFrame([dict(race_id='R1', horse_number=2, horse_name='馬A', date='2026-01-01', evidence_status='UNRESOLVED_NO_TIME', verified_start_status='UNKNOWN', training_eligible=False), dict(race_id='R1', horse_number=1, horse_name='馬B', date='2026-01-01', evidence_status='TIMED_FINISH', verified_start_status='UNKNOWN', training_eligible=False)])
        self.review = review_template(self.audit)
    def fill(self):
        self.review.loc[0, ['start_status','verified_source','source_reference','verified_at','reviewer']] = ['STARTED','official','record-1','2026-01-02T09:00:00+09:00','auditor']
    def test_blank_not_verified(self):
        enriched, verified = reconcile_review(self.audit, self.review)
        self.assertEqual(len(verified), 0)
        self.assertFalse(enriched.training_eligible.any())
    def test_started_no_time_still_not_training(self):
        self.fill(); enriched, verified = reconcile_review(self.audit, self.review)
        self.assertEqual(len(verified), 1)
        self.assertFalse(enriched.training_eligible.any())
    def test_missing_provenance_rejected(self):
        self.fill(); self.review.loc[0,'source_reference']=''
        with self.assertRaises(ValueError): validate_review(self.review, self.audit)
    def test_bad_timestamp_rejected(self):
        self.fill(); self.review.loc[0,'verified_at']='not a date'
        with self.assertRaises(ValueError): validate_review(self.review, self.audit)
    def test_wrong_horse_rejected(self):
        self.fill(); self.review.loc[0,'horse_name']='別馬'
        with self.assertRaises(ValueError): validate_review(self.review, self.audit)
    def test_duplicate_rejected(self):
        self.fill(); r=pd.concat([self.review,self.review],ignore_index=True)
        with self.assertRaises(ValueError): validate_review(r,self.audit)
    def test_unreviewed_partial_rejected(self):
        self.review.loc[0,'reviewer']='someone'
        with self.assertRaises(ValueError): validate_review(self.review,self.audit)

if __name__=='__main__': unittest.main()
