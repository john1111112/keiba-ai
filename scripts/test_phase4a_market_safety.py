"""Regression tests for no-post-race-data market eligibility."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
import pandas as pd
from src.market import compute_market_probabilities

class MarketSafetyTests(unittest.TestCase):
    def test_outcomes_do_not_affect_market_probabilities(self):
        base = pd.DataFrame({'race_id':['r','r','r'], 'odds':[2.,4.,8.],
                             'finish_order':[1,15,0], 'time_str':['1:20.0','-', '']})
        first = compute_market_probabilities(base)['p_market']
        changed = base.copy()
        changed['finish_order'] = [0,1,15]
        changed['time_str'] = ['', '1:21.0', '-']
        second = compute_market_probabilities(changed)['p_market']
        self.assertEqual(first.tolist(), second.tolist())
        self.assertAlmostEqual(first.sum(), 1.)
        self.assertTrue((first > 0).all())

    def test_pre_race_scratch_excluded(self):
        data = pd.DataFrame({'race_id':['r']*3, 'odds':[2.,4.,8.],
                             'pre_race_active':[True,False,True]})
        probs = compute_market_probabilities(data, active_col='pre_race_active')['p_market']
        self.assertEqual(probs.iloc[1], 0.)
        self.assertAlmostEqual(probs.sum(), 1.)

    def test_missing_active_odds_fails_closed_in_strict_mode(self):
        data = pd.DataFrame({'race_id':['r','r'], 'odds':[2.,None], 'active':[True,True]})
        with self.assertRaises(ValueError):
            compute_market_probabilities(data, active_col='active', strict=True)

    def test_missing_active_flag_fails(self):
        data = pd.DataFrame({'race_id':['r','r'], 'odds':[2.,3.], 'active':[True,None]})
        with self.assertRaises(ValueError):
            compute_market_probabilities(data, active_col='active')

if __name__ == '__main__':
    unittest.main()
