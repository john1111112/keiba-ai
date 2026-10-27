"""
第2回Windows統合開発: バックグラウンド自動監視・定期実行・安全フェイルクローズ 総合テストスイート

【検証項目】
1. 定期実行 (test_periodic_execution_and_cycles)
2. アプリ終了中の監視 (test_monitoring_runs_without_ui_process)
3. 特徴量不足 (test_insufficient_features_stops_prediction)
4. モデル読み込み失敗 (test_model_load_failure_safe_handling)
5. オッズ欠損・期限切れ (test_odds_missing_or_stale_blocked)
6. 期待値閾値判定 (test_ev_threshold_filtering)
7. 重複通知防止 (test_duplicate_alert_suppression)
8. Discord通信失敗 (test_discord_network_failure_resilience)
9. SQLiteの同時アクセス (test_concurrent_sqlite_access)
10. Windows再起動後の復帰 (test_windows_task_config_and_restart_recovery)
"""
import concurrent.futures
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.automated_monitor import (
    run_monitor_scan,
    init_alert_db,
    is_already_delivered,
    record_alert_delivery,
    load_alert_config,
    save_alert_config
)
from scripts.run_background_scan import run_cycle
from scripts.run_background_monitor import run_daemon
from src.prerace_feature_pipeline import build_prerace_28features, validate_clean_prerace_input

REPO_ROOT = Path(__file__).resolve().parent.parent


class UnattendedMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.state_db = self.dir_path / "test_alert_state.db"
        self.config_path = self.dir_path / "test_alert_config.json"
        init_alert_db(self.state_db)

        # テスト用基本設定
        self.base_config = {
            "min_ev": 1.15,
            "min_win_prob": 0.20,
            "max_odds_age_seconds": 120,
            "min_seconds_to_start": 300,
            "max_seconds_to_start": 1800,
            "windows_toast_enabled": False,  # テスト中はポップアップを抑止
            "discord_enabled": False,
            "safety_lock_enabled": False
        }
        save_alert_config(self.base_config, self.config_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_sample_feed(self, race_start_offset_minutes=15, odds_observed_offset_seconds=30,
                            win_odds=5.0, omit_odds=False, omit_race_course=False,
                            prohibited_key=None, feed_name="latest.json"):
        now = datetime.now(timezone.utc)
        race_start = now + timedelta(minutes=race_start_offset_minutes)
        odds_obs = now - timedelta(seconds=odds_observed_offset_seconds)
        pub_at = now - timedelta(seconds=min(10, odds_observed_offset_seconds // 2))

        horse_data = {
            "horse_number": 1,
            "horse_name": "テストスター",
            "bracket_number": 1,
            "age": 4,
            "weight_carry": 56.0,
            "sex": "牡",
            "horse_weight": 490.0,
            "horse_weight_diff": 2.0,
        }
        if not omit_odds:
            horse_data["win_odds"] = win_odds
            horse_data["odds_observed_at"] = odds_obs.isoformat()

        if prohibited_key:
            horse_data[prohibited_key] = 1

        race_data = {
            "race_id": "202610260101",
            "race_time": race_start.isoformat(),
            "race_status": "upcoming",
            "surface": "芝",
            "distance": 2000,
            "condition": "良",
            "direction": "右",
            "horses": [horse_data]
        }
        if not omit_race_course:
            race_data["race_course"] = "東京"

        feed = {
            "published_at": pub_at.isoformat(),
            "certified_source": True,
            "races": [race_data]
        }
        feed_file = self.dir_path / feed_name
        feed_file.write_text(json.dumps(feed, ensure_ascii=False, indent=2), encoding="utf-8")
        return feed_file

    # -------------------------------------------------------------
    # 1. 定期実行 (test_periodic_execution_and_cycles)
    # -------------------------------------------------------------
    def test_periodic_execution_and_cycles(self):
        feed_p = self._create_sample_feed()
        status_file = self.dir_path / "daemon_status.json"

        with patch("scripts.run_background_monitor.STATUS_PATH", status_file), \
             patch("scripts.run_background_monitor.PID_PATH", self.dir_path / "daemon.pid"), \
             patch("scripts.run_background_monitor.LOCK_PATH", self.dir_path / "daemon.lock"):
            run_daemon(
                manifest_path=str(feed_p),
                db_path=str(self.state_db),
                interval_seconds=1,
                max_cycles=3
            )
        self.assertTrue(status_file.exists())
        data = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(data["daemon_status"], "STOPPED")
        self.assertEqual(data["total_cycles"], 3)

    # -------------------------------------------------------------
    # 2. アプリ終了中の監視 (test_monitoring_runs_without_ui_process)
    # -------------------------------------------------------------
    def test_monitoring_runs_without_ui_process(self):
        feed_p = self._create_sample_feed()
        # UI（Streamlit）が起動していない独立環境で直接サイクル実行
        res = run_cycle(str(feed_p), str(self.state_db))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["processed_races"], 1)
        self.assertTrue(res["model_inference_allowed"])

    # -------------------------------------------------------------
    # 3. 特徴量不足 (test_insufficient_features_stops_prediction)
    # -------------------------------------------------------------
    def test_insufficient_features_stops_prediction(self):
        # 必須項目（race_course）が欠落したフィードを作成
        feed_p = self._create_sample_feed(omit_race_course=True)
        res = run_monitor_scan(feed_path=feed_p, state_db_path=self.state_db, config_path=self.config_path)
        self.assertEqual(res["status"], "COMPLETED")
        # 特徴量不足エラーが記録され、推論候補は0件
        self.assertEqual(res["candidates_found"], 0)
        self.assertTrue(any("INSUFFICIENT_FEATURES" in e for e in res["errors"]))

    # -------------------------------------------------------------
    # 4. モデル読み込み失敗 (test_model_load_failure_safe_handling)
    # -------------------------------------------------------------
    def test_model_load_failure_safe_handling(self):
        feed_p = self._create_sample_feed()
        fake_model = self.dir_path / "non_existent_model.joblib"
        res = run_monitor_scan(
            feed_path=feed_p,
            state_db_path=self.state_db,
            config_path=self.config_path,
            model_path=fake_model
        )
        self.assertEqual(res["status"], "MODEL_FILE_NOT_FOUND")
        self.assertFalse(res["model_inference_allowed"])
        self.assertEqual(res["notifications_sent"], 0)

    # -------------------------------------------------------------
    # 5. オッズ欠損・期限切れ (test_odds_missing_or_stale_blocked)
    # -------------------------------------------------------------
    def test_odds_missing_or_stale_blocked(self):
        # A: オッズ欠損時
        feed_missing = self._create_sample_feed(omit_odds=True, feed_name="missing_odds.json")
        res_m = run_monitor_scan(feed_path=feed_missing, state_db_path=self.state_db, config_path=self.config_path)
        self.assertEqual(res_m["candidates_found"], 0)
        self.assertEqual(res_m["notifications_sent"], 0)

        # B: オッズ期限切れ（5分前観測、上限120秒）
        feed_stale = self._create_sample_feed(odds_observed_offset_seconds=300, feed_name="stale_odds.json")
        res_s = run_monitor_scan(feed_path=feed_stale, state_db_path=self.state_db, config_path=self.config_path)
        self.assertEqual(res_s["candidates_found"], 0)
        self.assertEqual(res_s["notifications_sent"], 0)

    # -------------------------------------------------------------
    # 6. 期待値閾値判定 (test_ev_threshold_filtering)
    # -------------------------------------------------------------
    def test_ev_threshold_filtering(self):
        # 高オッズ（10.0倍） -> 勝率が一定以上あれば EV >= 1.15
        feed_high = self._create_sample_feed(win_odds=10.0, feed_name="high_ev.json")
        res = run_monitor_scan(feed_path=feed_high, state_db_path=self.state_db, config_path=self.config_path, dry_run=True)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["candidates_found"], 1)

        # 低オッズ（1.0倍） -> EV < 1.15 になるため eligible == 0
        feed_low = self._create_sample_feed(win_odds=1.0, feed_name="low_ev.json")
        res_low = run_monitor_scan(feed_path=feed_low, state_db_path=self.state_db, config_path=self.config_path, dry_run=True)
        self.assertEqual(res_low["eligible_candidates"], 0)

    # -------------------------------------------------------------
    # 7. 重複通知防止 (test_duplicate_alert_suppression)
    # -------------------------------------------------------------
    def test_duplicate_alert_suppression(self):
        feed_p = self._create_sample_feed(win_odds=10.0)
        with patch("src.automated_monitor.verify_feed_provenance", return_value=(True, "PROVENANCE_VERIFIED")), \
             patch("src.automated_monitor.send_windows_notification", return_value=True), \
             patch("src.notifier.send_windows_notification", return_value=True):
            # 1回目実行
            res1 = run_monitor_scan(feed_path=feed_p, state_db_path=self.state_db, config_path=self.config_path, dry_run=False)
            self.assertEqual(res1["notifications_sent"], 1)

            # 2回目実行（同一レース・同一条件）
            res2 = run_monitor_scan(feed_path=feed_p, state_db_path=self.state_db, config_path=self.config_path, dry_run=False)
            # 重複が抑止され、送信件数は 0 になる
            self.assertEqual(res2["notifications_sent"], 0)

    # -------------------------------------------------------------
    # 8. Discord通信失敗時の復旧 (test_discord_network_failure_resilience)
    # -------------------------------------------------------------
    def test_discord_network_failure_resilience(self):
        feed_p = self._create_sample_feed(win_odds=10.0)
        cfg = load_alert_config(self.config_path)
        cfg["discord_enabled"] = True
        cfg["windows_toast_enabled"] = True
        save_alert_config(cfg, self.config_path)

        # requests.post をモックして例外（ネットワークタイムアウト）を発生させる
        # windows_notification は成功をモック
        with patch("src.automated_monitor.verify_feed_provenance", return_value=(True, "PROVENANCE_VERIFIED")), \
             patch("src.automated_monitor.send_windows_notification", return_value=True), \
             patch("src.notifier.send_windows_notification", return_value=True), \
             patch("src.notifier.get_discord_webhook", return_value="https://discord.com/api/webhooks/test"), \
             patch("requests.post", side_effect=Exception("Connection timed out")):
            res = run_monitor_scan(feed_path=feed_p, state_db_path=self.state_db, config_path=self.config_path, dry_run=False)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertEqual(res["notifications_sent"], 1)

        # 送信ステータスが記録されており、エラーがあってもDB整合性が保たれていること
        with closing(sqlite3.connect(f"file:{self.state_db.resolve()}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT delivery_status, discord_status, error_message FROM alert_delivery").fetchone()
            self.assertEqual(row[0], "PARTIAL_SUCCESS")
            self.assertEqual(row[1], "FAILED")
            self.assertIn("DiscordError", row[2])

    # -------------------------------------------------------------
    # 9. SQLiteの同時アクセス安全性 (test_concurrent_sqlite_access)
    # -------------------------------------------------------------
    def test_concurrent_sqlite_access(self):
        # 複数スレッドから同時に読み書きを行ってもロックエラーでクラッシュしないこと
        def worker(thread_id):
            for i in range(10):
                record_alert_delivery(
                    db_path=self.state_db,
                    race_id=f"race_{thread_id}",
                    horse_number=i + 1,
                    horse_name=f"Horse_{i}",
                    event_type="TEST_EV",
                    win_odds=3.0,
                    win_probability=0.3,
                    expected_value=0.9,
                    channel="test",
                    status="DELIVERED"
                )
                is_already_delivered(self.state_db, f"race_{thread_id}", i + 1, "TEST_EV")

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker, tid) for tid in range(5)]
            for f in concurrent.futures.as_completed(futures):
                f.result()  # 例外があれば raise される

        with closing(sqlite3.connect(f"file:{self.state_db.resolve()}?mode=ro", uri=True)) as conn:
            count = conn.execute("SELECT count(*) FROM alert_delivery").fetchone()[0]
            self.assertEqual(count, 50)

    # -------------------------------------------------------------
    # 10. Windows再起動後の復帰・タスク設定 (test_windows_task_config_and_restart_recovery)
    # -------------------------------------------------------------
    def test_windows_task_config_and_restart_recovery(self):
        # タスクスケジューラ登録スクリプトの存在確認
        ps1_script = REPO_ROOT / "scripts" / "setup_windows_task.ps1"
        bat_script = REPO_ROOT / "scripts" / "setup_windows_task.bat"
        self.assertTrue(ps1_script.is_file())
        self.assertTrue(bat_script.is_file())

        content = ps1_script.read_text(encoding="utf-8")
        self.assertIn("schtasks.exe", content)
        self.assertIn("KeibaAI_BackgroundMonitor", content)

        # 再起動復帰シミュレーション: 前回の PID ファイルが残った状態で再起動しても正常稼働できること
        pid_file = self.dir_path / "daemon.pid"
        pid_file.write_text("999999", encoding="utf-8")  # 架空の旧PID
        status_file = self.dir_path / "daemon_status.json"

        feed_p = self._create_sample_feed()
        with patch("scripts.run_background_monitor.STATUS_PATH", status_file), \
             patch("scripts.run_background_monitor.PID_PATH", pid_file), \
             patch("scripts.run_background_monitor.LOCK_PATH", self.dir_path / "daemon.lock"):
            run_daemon(manifest_path=str(feed_p), db_path=str(self.state_db), interval_seconds=1, max_cycles=1)

        data = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(data["total_cycles"], 1)


if __name__ == "__main__":
    unittest.main()
