"""Offline checks prove missing prerequisites are visible, not called ready.

No JRA-VAN, Discord, or Windows registration is invoked by these tests.
"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.check_release_readiness import check


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "models").mkdir()
        (self.root / "data" / "authorized_feed").mkdir(parents=True)

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_unconfigured_environment_is_not_ready(self):
        report = check(self.root, include_os_probe=False)
        self.assertFalse(report["ready_for_human_acceptance"])
        self.assertEqual(report["machine_checkable_passed"], 0)
        self.assertEqual(report["machine_checkable_total"], 8)
        self.assertEqual(
            {c["status"] for c in report["checks"]}, {"PENDING"}
        )
        self.assertIn("no data lab", report["note"].lower().replace("-", " "))

    def test_real_recorded_success_still_requires_sdk_and_scheduler_on_machine(self):
        model = self.root / "models" / "keiba_lgbm_win_v2_28cols.joblib"
        blob = b"local-model-fixture"
        model.write_bytes(blob)
        self.put("models/keiba_lgbm_win_v2_28cols.joblib.sha256", {
            "sha256": hashlib.sha256(blob).hexdigest()
        })
        self.put("data/alert_config.json", {
            "discord_enabled": True, "safety_lock_enabled": False
        })
        self.put("data/discord_config.json", {
            "webhook_url": "https://discord.com/api/webhooks/000/TEST"
        })
        stamp = datetime.now(timezone.utc).isoformat()
        self.put("data/monitor_daemon_status.json", {
            "run_mode": "WINDOWS_TASK_SCHEDULER",
            "daemon_status": "RUNNING", "last_run_at": stamp
        })
        self.put("data/last_background_scan.json", {
            "status": "COMPLETED", "is_certified_source": True,
            "checked_at": stamp, "processed_races": 12,
            "notifications_sent": 1
        })
        self.put("data/authorized_feed/latest_jvlink_feed.json", {"races": []})
        db = self.root / "data" / "alert_state.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE alert_delivery "
                "(id INTEGER PRIMARY KEY, first_sent_at TEXT,"
                " race_id TEXT, horse_number INTEGER, discord_status TEXT)"
            )
            conn.execute(
                "INSERT INTO alert_delivery VALUES (1, ?, 'RACE1', 3, 'DELIVERED')",
                (stamp,)
            )
        report = check(self.root, include_os_probe=False)
        checks = {c["code"]: c for c in report["checks"]}
        for code in (
            "MODEL_FILE", "DISCORD_CONFIG", "RUNNING_WHILE_UI_CLOSED",
            "REAL_JRA_FEED", "REAL_AI_INFERENCE", "DISCORD_SERVER_ACCEPTANCE"
        ):
            self.assertEqual(checks[code]["status"], "PASS", code)
        self.assertEqual(report["machine_checkable_passed"], 6)
        self.assertEqual(checks["SDK_AND_COM"]["status"], "PENDING")
        self.assertEqual(checks["SCHEDULED_TASK"]["status"], "PENDING")
        self.assertFalse(report["ready_for_human_acceptance"])

    def test_stale_monitor_and_old_race_data_never_mark_ready(self):
        self.put("data/monitor_daemon_status.json", {
            "run_mode": "WINDOWS_TASK_SCHEDULER", "daemon_status": "RUNNING",
            "last_run_at": "2026-01-01T00:00:00+00:00"
        })
        self.put("data/last_background_scan.json", {
            "status": "COMPLETED", "is_certified_source": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "processed_races": 12
        })
        report = check(self.root, include_os_probe=False)
        states = {c["code"]: c["status"] for c in report["checks"]}
        self.assertEqual(states["RUNNING_WHILE_UI_CLOSED"], "PENDING")
        self.assertEqual(states["REAL_JRA_FEED"], "PENDING")
        self.assertEqual(states["REAL_AI_INFERENCE"], "PENDING")

    def test_release_checker_never_sends_a_notification_or_calls_jvinit(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "check_release_readiness.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(".JVInit(", script)
        self.assertNotIn(".JVRTOpen(", script)
        self.assertNotIn("requests.post(", script)
        launcher = (
            Path(__file__).resolve().parents[1] / "CHECK_RELEASE.bat"
        ).read_text(encoding="utf-8")
        self.assertIn("check_release_readiness.py", launcher)


if __name__ == "__main__":
    unittest.main()
