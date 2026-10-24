import sqlite3
import tempfile
import unittest
from pathlib import Path
from src.paper_trading import capture, settle
from src.paper_dashboard import load_dashboard

T="2026-10-24T01:00:00+00:00"

class DashboardTests(unittest.TestCase):
    def test_missing_db_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"missing.db"
            self.assertIsNone(load_dashboard(p))
            self.assertFalse(p.exists())

    def test_aggregation_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"paper.db"
            capture(p,[dict(race_id="R1",horse_number=1,prediction_at=T,cutoff_at="2026-10-24T02:00:00Z",win_probability=.2,odds=5,odds_observed_at="2026-10-24T00:30:00Z",odds_fetched_at="2026-10-24T00:40:00Z",model_id="M",source="test")])
            settle(p,[dict(
                race_id="R1",horse_number=1,prediction_at=T,
                won=1,official_payout_yen=500,settled_at="2026-10-24T03:00:00Z",
                source_url="https://www.jra.go.jp/official",verified_by="auditor",verification_status="verified"
            )])
            before=p.read_bytes()
            x=load_dashboard(p)
            self.assertEqual(x["overall"]["roi"],5.0)
            self.assertEqual(x["models"][0]["model_id"],"M")
            self.assertEqual(before,p.read_bytes())

    def test_zero_settlements_roi_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"paper.db"
            capture(p,[dict(race_id="R1",horse_number=1,prediction_at=T,cutoff_at="2026-10-24T02:00:00Z",win_probability=.2,odds=5,odds_observed_at="2026-10-24T00:30:00Z",odds_fetched_at="2026-10-24T00:40:00Z",model_id="M",source="test")])
            x=load_dashboard(p)
            self.assertIsNone(x["overall"]["roi"])
            self.assertEqual(x["overall"]["unsettled"],1)

if __name__ == "__main__": unittest.main()
