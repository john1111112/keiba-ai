"""Regression tests for temporal leakage in Phase 3D odds history."""
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.odds_history import record_odds_snapshot, get_latest_odds_as_of


def test_no_future_received_odds():
    with TemporaryDirectory() as temp:
        db = Path(temp) / 'odds.db'
        rows = [
            dict(race_id='R1', horse_number=1, odds_value=5.0,
                 observed_at='2026-10-24T15:00:00+09:00', fetched_at='2026-10-24T15:01:00+09:00', source='test'),
            dict(race_id='R1', horse_number=1, odds_value=3.0,
                 observed_at='2026-10-24T15:05:00+09:00', fetched_at='2026-10-24T15:12:00+09:00', source='test'),
            dict(race_id='R1', horse_number=1, odds_value=1.5,
                 observed_at='2026-10-24T15:07:00+09:00', fetched_at='2026-10-24T15:08:00+09:00', source='test', data_status='final_settled'),
            dict(race_id='R1', horse_number=2, odds_value=8.0,
                 observed_at='2026-10-24T15:00:00+09:00', fetched_at='2026-10-24T15:01:00+09:00', source='test'),
        ]
        assert record_odds_snapshot(rows, db) == 4
        early = get_latest_odds_as_of('R1', '2026-10-24T15:10:00+09:00', db_path=db)
        assert early.set_index('horse_number').loc[1, 'odds_value'] == 5.0
        assert len(early) == 2
        late = get_latest_odds_as_of('R1', '2026-10-24T15:15:00+09:00', db_path=db)
        assert late.set_index('horse_number').loc[1, 'odds_value'] == 3.0
        # Same publication time, newer receipt: select exactly one row.
        assert record_odds_snapshot([dict(rows[0], odds_value=4.5, fetched_at='2026-10-24T15:02:00+09:00')], db) == 1
        tied = get_latest_odds_as_of('R1', '2026-10-24T15:03:00+09:00', db_path=db)
        assert len(tied) == 2
        assert tied.set_index('horse_number').loc[1, 'odds_value'] == 4.5


def test_reject_missing_and_invalid_timestamps():
    with TemporaryDirectory() as temp:
        db = Path(temp) / 'odds.db'
        base = dict(race_id='R2', horse_number=1, odds_value=3.5, source='test')
        assert record_odds_snapshot([dict(base, observed_at='2026-10-24T15:00:00+09:00')], db) == 0
        assert record_odds_snapshot([dict(base, observed_at='2026-10-24T15:10:00+09:00', fetched_at='2026-10-24T15:00:00+09:00')], db) == 0
        assert record_odds_snapshot([dict(base, observed_at='not-a-date', fetched_at='2026-10-24T15:00:00+09:00')], db) == 0


if __name__ == '__main__':
    test_no_future_received_odds()
    test_reject_missing_and_invalid_timestamps()
    print('odds history safety tests passed')
