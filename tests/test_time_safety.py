import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.odds_history import record_odds_snapshot, get_latest_odds_as_of

class OddsTimeSafety(unittest.TestCase):
    def test_future_receipt_and_final_and_sample_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'odds.db'
            base = dict(race_id='R', horse_number=1, source='jra_van',
                        observed_at='2026-10-24T14:00:00+09:00',
                        fetched_at='2026-10-24T14:01:00+09:00',
                        data_status='pre_race_flash')
            rows = [dict(base, odds_value=8),
                    dict(base, odds_value=2, observed_at='2026-10-24T14:02:00+09:00', fetched_at='2026-10-24T14:15:00+09:00'),
                    dict(base, odds_value=1.2, data_status='final_settled'),
                    dict(base, odds_value=1.5, data_status='sample_only', source='sample'),
                    dict(base, odds_value=3, source='sample')]
            self.assertEqual(record_odds_snapshot(rows, db), 5)
            before = get_latest_odds_as_of('R', '2026-10-24T14:10:00+09:00', db_path=db)
            self.assertEqual(len(before), 1)
            self.assertEqual(before.iloc[0]['odds_value'], 8)
            after = get_latest_odds_as_of('R', '2026-10-24T14:20:00+09:00', db_path=db)
            self.assertEqual(after.iloc[0]['odds_value'], 2)
    def test_offsets_compare_as_instant(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'odds.db'
            self.assertEqual(record_odds_snapshot([dict(race_id='R',horse_number=2,odds_value=5,
                observed_at='2026-10-24T05:00:00Z', fetched_at='2026-10-24T05:01:00Z',
                source='jra_van', data_status='pre_race_flash')], db),1)
            self.assertEqual(len(get_latest_odds_as_of('R','2026-10-24T14:02:00+09:00',db_path=db)),1)
            self.assertEqual(len(get_latest_odds_as_of('R','2026-10-24T14:00:30+09:00',db_path=db)),0)

if __name__=='__main__': unittest.main()
