import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd

from scripts.run_prerace_auto_recorder import run_schedule_scan


class ScheduleScanSafetyTests(unittest.TestCase):
    def test_unbounded_tolerance_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                run_schedule_scan(manifest, Path(folder) / "odds.db",
                                  tolerance_minutes=1440)

    def test_historical_parquet_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            historical = Path(folder) / "features.parquet"
            historical.touch()
            with patch("scripts.run_prerace_auto_recorder.pd.read_parquet") as read, patch(
                "scripts.run_prerace_auto_recorder.record_prerace_snapshot"
            ) as record:
                report = run_schedule_scan(historical, Path(folder) / "odds.db")
            read.assert_not_called()
            record.assert_not_called()
            self.assertEqual(report["status"], "BLOCKED_NOT_RACE_MANIFEST")

    def test_missing_race_time_never_defaults_to_fabricated_start(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text(json.dumps([{"race_id": "r1", "race_status": "upcoming",
                                            "race_time": None}]), encoding="utf-8")
            with patch("scripts.run_prerace_auto_recorder.record_prerace_snapshot") as record:
                report = run_schedule_scan(manifest, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T06:00:00+00:00")
            record.assert_not_called()
            self.assertEqual(report["snapshots"][0]["status"], "SKIPPED_MISSING_RACE_TIME")

    def test_naive_race_time_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text(json.dumps([{"race_id": "r1", "race_status": "upcoming",
                                            "race_time": "2026-10-25 15:40:00"}]), encoding="utf-8")
            with patch("scripts.run_prerace_auto_recorder.record_prerace_snapshot") as record:
                report = run_schedule_scan(manifest, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T06:00:00+00:00")
            record.assert_not_called()
            self.assertEqual(report["snapshots"][0]["status"], "SKIPPED_INVALID_RACE_TIME")

    def test_elapsed_race_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text(json.dumps([{"race_id": "r1", "race_status": "upcoming",
                                            "race_time": "2026-10-25T05:00:00+00:00"}]), encoding="utf-8")
            with patch("scripts.run_prerace_auto_recorder.record_prerace_snapshot") as record:
                report = run_schedule_scan(manifest, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T06:00:00+00:00")
            record.assert_not_called()
            self.assertEqual(report["snapshots"][0]["status"], "SKIPPED_INVALID_RACE_TIME")

    def test_all_timings_skips_future_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text(json.dumps([{"race_id": "r1", "race_status": "upcoming",
                                            "race_time": "2026-10-25T15:40:00+09:00"}]), encoding="utf-8")
            with patch("scripts.run_prerace_auto_recorder.record_prerace_snapshot",
                       return_value={"odds_status": "missing", "status": "RECORDED"}) as record:
                report = run_schedule_scan(manifest, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T15:12:00+09:00",
                                           all_timings=True)
            self.assertEqual(record.call_count, 1)
            self.assertEqual(sum(row["status"] == "SKIPPED_FUTURE_TARGET"
                                 for row in report["snapshots"]), 2)

    def test_explicit_json_manifest_scans_without_parquet(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text(json.dumps([{
                "race_id": "r1", "race_status": "upcoming",
                "race_time": "2026-10-25T15:40:00+09:00"
            }]), encoding="utf-8")
            with patch("scripts.run_prerace_auto_recorder.pd.read_parquet") as read_parquet, patch(
                "scripts.run_prerace_auto_recorder.record_prerace_snapshot",
                return_value={"odds_status": "missing", "status": "RECORDED"}
            ) as record:
                result = run_schedule_scan(manifest, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T15:10:00+09:00")
            read_parquet.assert_not_called()
            record.assert_called_once()
            self.assertEqual(result["scanned_races_count"], 1)

    def test_json_manifest_without_upcoming_flag_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text(json.dumps([{
                "race_id": "r1", "race_time": "2026-10-25T15:40:00+09:00"
            }]), encoding="utf-8")
            with patch("scripts.run_prerace_auto_recorder.record_prerace_snapshot") as record:
                result = run_schedule_scan(manifest, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T15:10:00+09:00")
            record.assert_not_called()
            self.assertEqual(result["status"], "BLOCKED_NO_UPCOMING_MANIFEST")

    def test_conflicting_race_starts_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text(json.dumps([
                {"race_id": "r1", "race_status": "upcoming",
                 "race_time": "2026-10-25T15:40:00+09:00"},
                {"race_id": "r1", "race_status": "upcoming",
                 "race_time": "2026-10-25T15:50:00+09:00"}
            ]), encoding="utf-8")
            with patch("scripts.run_prerace_auto_recorder.record_prerace_snapshot") as record:
                with self.assertRaises(ValueError):
                    run_schedule_scan(manifest, Path(folder) / "odds.db",
                                      as_of_time="2026-10-25T15:10:00+09:00")
            record.assert_not_called()

    def test_missing_manifest_blocks_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text(json.dumps([{
                "race_id": "r1", "race_time": "2026-10-25T15:40:00+09:00"
            }]), encoding="utf-8")
            with patch("scripts.run_prerace_auto_recorder.record_prerace_snapshot") as record:
                result = run_schedule_scan(manifest, Path(folder) / "odds.db",
                                           as_of_time="2026-10-25T15:35:00+09:00")
            record.assert_not_called()
            self.assertEqual(result["status"], "BLOCKED_NO_UPCOMING_MANIFEST")

    def test_naive_asof_time_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "upcoming.json"
            manifest.write_text(json.dumps([{
                "race_id": "r1", "race_status": "upcoming",
                "race_time": "2026-10-25T15:40:00+09:00"
            }]), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_schedule_scan(manifest, Path(folder) / "odds.db",
                                  as_of_time="2026-10-25T06:00:00")


if __name__ == "__main__":
    unittest.main()
