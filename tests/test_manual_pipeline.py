"""End-to-end tests of the no-cost manual pre-race dashboard pipeline."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_manual_prerace_pipeline import run_manual_pipeline


class ManualPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "snapshot.json"
        self.db = self.root / "odds.db"
        self.out = self.root / "dashboard.html"
        self.snapshot = {
            "race_id": "r1", "race_start_time": "2026-10-25T15:40:00+09:00",
            "observed_at": "2026-10-25T15:30:00+09:00",
            "fetched_at": "2026-10-25T15:30:05+09:00",
            "source": "manual_screen",
            "odds": [{"horse_number": 1, "odds_value": 5.0},
                     {"horse_number": 2, "odds_value": 12.0}],
        }
        self.source.write_text(json.dumps(self.snapshot), encoding="utf-8")

    def test_import_and_render_no_bet(self):
        result = run_manual_pipeline(self.source, self.db, self.out)
        self.assertEqual(result["status"], "DASHBOARD_CREATED")
        self.assertEqual(result["bet_status"], "NO_BET")
        self.assertEqual(result["readiness"]["eligible_odds_horses"], 2)
        self.assertEqual(result["readiness"]["manual_or_uncertified_odds"], 2)
        self.assertIn("NO BET", self.out.read_text(encoding="utf-8"))
        self.assertEqual(run_manual_pipeline(self.source, self.db, self.out)
                         ["import"]["inserted_count"], 0)

    def test_dry_run_never_creates_db_or_html(self):
        result = run_manual_pipeline(self.source, self.db, self.out, dry_run=True)
        self.assertEqual(result["status"], "VALIDATED_ONLY")
        self.assertFalse(self.db.exists())
        self.assertFalse(self.out.exists())

    def test_future_asof_rejected_without_write(self):
        with self.assertRaises(ValueError):
            run_manual_pipeline(self.source, self.db, self.out,
                                as_of_time="2026-10-25T15:41:00+09:00")
        self.assertFalse(self.db.exists())

    def test_asof_before_fetch_rejected_without_write(self):
        with self.assertRaises(ValueError):
            run_manual_pipeline(self.source, self.db, self.out,
                                as_of_time="2026-10-25T15:29:00+09:00")
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
