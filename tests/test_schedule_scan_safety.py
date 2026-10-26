import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd

from scripts.run_prerace_auto_recorder import run_schedule_scan


class ScheduleScanSafetyTests(unittest.TestCase):
    def test_missing_race_time_never_defaults_to_fabricated_start(self):
        with tempfile.TemporaryDirectory() as folder:
            features = Path(folder) / "features.parquet"
            db = Path(folder) / "odds.db"
            fake = pd.DataFrame([{"race_id": "r1", "finish_order": 0, "race_status": "upcoming",
                                  "date": "2026-10-25", "race_time": None,
                                  "start_time": None}])
            with patch("scripts.run_prerace_auto_recorder.pd.read_parquet", return_value=fake), patch(
                "scripts.run_prerace_auto_recorder.record_prerace_snapshot"
            ) as record:
                features.touch()
                report = run_schedule_scan(features, db, as_of_time="2026-10-25T06:00:00+00:00")
            record.assert_not_called()
            self.assertEqual(report["missing_count"], 1)
            self.assertEqual(report["snapshots"][0]["status"], "SKIPPED_MISSING_RACE_TIME")

    def test_naive_race_time_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            features = Path(folder) / "features.parquet"
            features.touch()
            fake = pd.DataFrame([{"race_id": "r1", "finish_order": 0, "race_status": "upcoming",
                                  "race_time": "2026-10-25 15:40:00"}])
            with patch("scripts.run_prerace_auto_recorder.pd.read_parquet", return_value=fake), patch(
                "scripts.run_prerace_auto_recorder.record_prerace_snapshot"
            ) as record:
                result = run_schedule_scan(features, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T06:00:00+00:00")
            record.assert_not_called()
            self.assertEqual(result["snapshots"][0]["status"], "SKIPPED_INVALID_RACE_TIME")

    def test_elapsed_race_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            features = Path(folder) / "features.parquet"
            features.touch()
            fake = pd.DataFrame([{"race_id": "r1", "finish_order": 0, "race_status": "upcoming",
                                  "race_time": "2026-10-25T05:00:00+00:00"}])
            with patch("scripts.run_prerace_auto_recorder.pd.read_parquet", return_value=fake), patch(
                "scripts.run_prerace_auto_recorder.record_prerace_snapshot"
            ) as record:
                result = run_schedule_scan(features, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T06:00:00+00:00")
            record.assert_not_called()
            self.assertEqual(result["snapshots"][0]["status"], "SKIPPED_INVALID_RACE_TIME")

    def test_all_timings_skips_future_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            features = Path(folder) / "features.parquet"
            features.touch()
            fake = pd.DataFrame([{"race_id": "r1", "finish_order": 0, "race_status": "upcoming",
                                  "race_time": "2026-10-25T15:40:00+09:00"}])
            with patch("scripts.run_prerace_auto_recorder.pd.read_parquet", return_value=fake), patch(
                "scripts.run_prerace_auto_recorder.record_prerace_snapshot",
                return_value={"odds_status": "missing", "status": "RECORDED"}
            ) as record:
                result = run_schedule_scan(features, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T15:12:00+09:00",
                                           all_timings=True)
            self.assertEqual(record.call_count, 1)
            self.assertEqual(sum(r["status"] == "SKIPPED_FUTURE_TARGET"
                                 for r in result["snapshots"]), 2)

    def test_missing_manifest_blocks_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            features = Path(folder) / "features.parquet"
            features.touch()
            fake = pd.DataFrame([{"race_id": "r1", "finish_order": 0,
                                  "race_time": "2026-10-25T15:40:00+09:00"}])
            with patch("scripts.run_prerace_auto_recorder.pd.read_parquet", return_value=fake), patch(
                "scripts.run_prerace_auto_recorder.record_prerace_snapshot"
            ) as record:
                result = run_schedule_scan(features, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T15:35:00+09:00")
            record.assert_not_called()
            self.assertEqual(result["status"], "BLOCKED_NO_UPCOMING_MANIFEST")

    def test_naive_asof_time_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            features = Path(folder) / "features.parquet"
            features.touch()
            fake = pd.DataFrame([{"race_id": "r1", "finish_order": 0, "race_status": "upcoming",
                                  "race_time": "2026-10-25T15:40:00+09:00"}])
            with patch("scripts.run_prerace_auto_recorder.pd.read_parquet", return_value=fake):
                with self.assertRaises(ValueError):
                    run_schedule_scan(features, Path(folder) / "odds.db",
                                      as_of_time="2026-10-25T06:00:00")


if __name__ == "__main__":
    unittest.main()
