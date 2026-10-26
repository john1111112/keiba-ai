"""Regression tests for unattended scheduled JV-Link acquisition.

Tests use synthetic local feeds and never contact JRA-VAN or send alerts.
"""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_background_scan import run_cycle, main


class ScheduledLiveScanTest(unittest.TestCase):
    def test_windows_scheduled_runner_requests_fresh_jvlink(self):
        repo = Path(__file__).resolve().parents[1]
        content = (repo / "scripts" / "run_scheduled_task.bat").read_text(encoding="utf-8")
        self.assertIn("--use-jvlink", content)
        self.assertIn("latest_jvlink_feed.json", content)
        self.assertNotIn("authorized_feed\\latest.json", content)
        self.assertIn("exit /b %ERRORLEVEL%", content)

    def test_live_cycle_reads_just_acquired_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            feed_path = folder / "latest_jvlink_feed.json"
            manifest = folder / "unused.json"
            db = folder / "alerts.db"
            expected = {
                "status": "SCAN_OK", "processed_races": 1,
                "model_inference_allowed": True, "eligible_candidates": 1,
                "notifications_sent": 0, "candidates_found": 1
            }

            def write_feed(**kwargs):
                feed_path.write_text(
                    json.dumps({"published_at": "2026-10-27T01:00:00+00:00", "races": []}),
                    encoding="utf-8",
                )
                self.assertEqual(kwargs["output_dir"], folder)
                return {"races": []}

            with patch("src.jvlink_client.JVLinkClient") as client, patch(
                "scripts.run_background_scan.run_monitor_scan",
                return_value=expected
            ) as scan:
                client.return_value.fetch_and_build_live_feed.side_effect = write_feed
                result = run_cycle(manifest, db, use_jvlink=True, dry_run=True)
                scan.assert_called_once()
                self.assertEqual(scan.call_args.kwargs["feed_path"], feed_path)
                self.assertTrue(scan.call_args.kwargs["dry_run"])
                self.assertEqual(result["status"], "SCAN_OK")
                self.assertEqual(result["captured_count"], 1)
                self.assertEqual(result["bet_status"], "NO_BET")

    def test_failed_acquisition_cannot_reuse_previous_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "latest_jvlink_feed.json").write_text(
                '{"races":[]}', encoding="utf-8"
            )
            with patch("src.jvlink_client.JVLinkClient") as client, patch(
                "scripts.run_background_scan.run_monitor_scan"
            ) as scan:
                client.return_value.fetch_and_build_live_feed.side_effect = RuntimeError(
                    "provider unavailable"
                )
                result = run_cycle(folder, folder / "alerts.db", use_jvlink=True)
                self.assertEqual(result["status"], "JVLINK_FETCH_FAILED")
                self.assertFalse(result["model_inference_allowed"])
                self.assertEqual(result["captured_count"], 0)
                scan.assert_not_called()

    def test_scheduled_cli_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            with patch("scripts.run_background_scan.run_cycle", return_value={
                "status": "JVLINK_FETCH_FAILED", "reason": "service unavailable"
            }):
                rc = main(["--manifest", "missing.json", "--db",
                           str(Path(tmp) / "alerts.db"), "--out", str(out),
                           "--use-jvlink"])
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(out.read_text())["status"], "JVLINK_FETCH_FAILED")

    def test_scheduled_cli_clean_scan_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("scripts.run_background_scan.run_cycle", return_value={
                "status": "NO_UPCOMING_RACES", "processed_races": 0,
                "bet_status": "NO_BET"
            }):
                rc = main(["--out", str(Path(tmp) / "clean.json")])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
