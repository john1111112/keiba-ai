"""Regression tests for unattended scheduled JV-Link acquisition.

Tests use synthetic local feeds and never contact JRA-VAN or send alerts.
"""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_background_scan import run_cycle, main
from scripts.enable_personal_monitor import enable_monitor_config


class ScheduledLiveScanTest(unittest.TestCase):
    def test_windows_scheduled_runner_requests_fresh_jvlink(self):
        repo = Path(__file__).resolve().parents[1]
        content = (repo / "scripts" / "run_scheduled_task.bat").read_text(encoding="utf-8")
        self.assertIn("--use-jvlink", content)
        self.assertIn("latest_jvlink_feed.json", content)
        self.assertNotIn("authorized_feed\\latest.json", content)
        self.assertIn("exit /b %ERRORLEVEL%", content)

    def test_registration_is_independent_of_opening_the_dashboard(self):
        repo = Path(__file__).resolve().parents[1]
        installer = (repo / "INSTALL_AUTO_MONITOR.bat").read_text(encoding="utf-8")
        scheduler = (repo / "scripts" / "setup_windows_task.ps1").read_text(encoding="utf-8")
        self.assertIn("setup_windows_task.ps1", installer)
        self.assertIn("-IntervalMinutes 1", installer)
        self.assertIn("enable_personal_monitor.py", installer)
        self.assertIn("/SC MINUTE", scheduler)
        self.assertIn("/MO $IntervalMinutes", scheduler)
        self.assertIn("run_scheduled_task.bat", scheduler)
        self.assertNotIn("-File \"app.py\"", installer)

    def test_opening_dashboard_does_not_start_duplicate_monitor(self):
        repo = Path(__file__).resolve().parents[1]
        launcher = (repo / "START_KEIBA_AI.bat").read_text(encoding="utf-8")
        self.assertIn("KeibaAI_BackgroundMonitor", launcher)
        self.assertNotIn("scripts\\run_background_monitor.py", launcher)
        self.assertIn("streamlit run app.py", launcher)

    def test_one_time_install_enables_buy_candidate_discord_without_losing_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({
                    "min_ev": 1.35, "min_win_prob": 0.3,
                    "discord_enabled": False, "safety_lock_enabled": True
                }), encoding="utf-8"
            )
            config = enable_monitor_config(path)
            self.assertEqual(config["min_ev"], 1.35)
            self.assertEqual(config["min_win_prob"], 0.3)
            self.assertTrue(config["discord_enabled"])
            self.assertFalse(config["safety_lock_enabled"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), config)

    def test_scheduled_scan_records_heartbeat_for_ui_without_ui_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "last_background_scan.json"
            with patch("scripts.run_background_scan.run_cycle", return_value={
                "status": "COMPLETED",
                "processed_races": 12, "eligible_candidates": 2,
                "notifications_sent": 1, "model_inference_allowed": True
            }):
                rc = main(["--out", str(out), "--use-jvlink"])
            self.assertEqual(rc, 0)
            beat = json.loads((Path(tmp) / "monitor_daemon_status.json").read_text(encoding="utf-8"))
            self.assertEqual(beat["run_mode"], "WINDOWS_TASK_SCHEDULER")
            self.assertEqual(beat["daemon_status"], "RUNNING")
            self.assertEqual(beat["last_scan_result"]["processed_races"], 12)
            self.assertEqual(beat["last_scan_result"]["notifications_sent"], 1)

    def test_scheduled_failed_acquisition_heartbeat_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("scripts.run_background_scan.run_cycle", return_value={
                "status": "JVLINK_FETCH_FAILED", "reason": "provider down"
            }):
                rc = main(["--out", str(Path(tmp) / "last_background_scan.json"), "--use-jvlink"])
            self.assertEqual(rc, 1)
            beat = json.loads((Path(tmp) / "monitor_daemon_status.json").read_text(encoding="utf-8"))
            self.assertEqual(beat["daemon_status"], "ERROR_RECOVERING")
            self.assertEqual(beat["last_scan_result"]["status"], "JVLINK_FETCH_FAILED")

    def test_live_cycle_reads_just_acquired_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            feed_path = folder / "latest_jvlink_feed.json"
            manifest = folder / "unused.json"
            db = folder / "alerts.db"
            expected = {
                "status": "SCAN_OK", "processed_races": 1,
                "model_inference_allowed": True, "eligible_candidates": 1,
                "notifications_sent": 0, "candidates_found": 1,
                "unsent_buy_candidates": 1, "missing_discord_webhook": 1,
                "discord_delivery_failures": 0,
                "is_certified_source": True
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
                self.assertEqual(result["unsent_buy_candidates"], 1)
                self.assertEqual(result["missing_discord_webhook"], 1)
                self.assertTrue(result["is_certified_source"])
                self.assertEqual(result["bet_status"], "NO_BET")

    def test_no_races_published_is_quiet_success_and_never_reuses_feed(self):
        from src.jvlink_client import JVLinkNoDataError
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "latest_jvlink_feed.json").write_text('{"races":[]}')
            with patch("src.jvlink_client.JVLinkClient") as client, patch(
                "scripts.run_background_scan.run_monitor_scan"
            ) as scan:
                client.return_value.fetch_and_build_live_feed.side_effect = (
                    JVLinkNoDataError("no races")
                )
                result = run_cycle(folder, folder / "alerts.db", use_jvlink=True)
                self.assertEqual(result["status"], "NO_UPCOMING_RACES")
                self.assertEqual(result["notifications_sent"], 0)
                scan.assert_not_called()

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

    def test_live_task_no_manifest_is_failure_but_legacy_scan_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("scripts.run_background_scan.run_cycle", return_value={
                "status": "NO_UPCOMING_MANIFEST", "processed_races": 0,
                "model_inference_allowed": False
            }):
                out = str(Path(tmp) / "out.json")
                self.assertEqual(main(["--out", out, "--use-jvlink"]), 1)
                self.assertEqual(main(["--out", out]), 0)

    def test_all_inference_failures_surface_as_failed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("scripts.run_background_scan.run_cycle", return_value={
                "status": "INFERENCE_BLOCKED", "processed_races": 0,
                "errors": ["Missing historical feature data"],
                "model_inference_allowed": False
            }):
                out = Path(tmp) / "last_background_scan.json"
                rc = main(["--out", str(out), "--use-jvlink"])
                self.assertEqual(rc, 1)
                self.assertEqual(
                    json.loads((Path(tmp) / "monitor_daemon_status.json").read_text(
                        encoding="utf-8"
                    ))["daemon_status"],
                    "ERROR_RECOVERING"
                )

    def test_buy_candidate_detected_but_discord_missing_is_visible_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "last_background_scan.json"
            with patch("scripts.run_background_scan.run_cycle", return_value={
                "status": "COMPLETED", "processed_races": 4,
                "eligible_candidates": 1, "notifications_sent": 0,
                "unsent_buy_candidates": 1, "missing_discord_webhook": 1,
                "discord_delivery_failures": 0
            }):
                rc = main(["--out", str(out), "--use-jvlink"])
            self.assertEqual(rc, 1)
            status = json.loads(
                (Path(tmp) / "monitor_daemon_status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status["daemon_status"], "ERROR_RECOVERING")
            self.assertEqual(status["last_scan_result"]["unsent_buy_candidates"], 1)

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
