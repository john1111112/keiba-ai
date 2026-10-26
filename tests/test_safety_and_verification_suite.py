"""
第3回一括開発: 安全性・統一検証・未来情報混入防止・排他制御 10大検証テストスイート

【検証項目】
1. 不正なJSON・スキーマ・時刻を拒絶する (test_reject_invalid_feed_json)
2. 出所未確認データでは通知しない (test_unconfirmed_source_blocks_notification)
3. 未来の成績が予測結果に混入しない (test_prevent_future_leakage_in_past_stats)
4. 対象日（開催日）がない場合は推論しない (test_missing_race_date_stops_inference)
5. 必須特徴量の欠損時は推論しない (test_missing_required_features_stops_inference)
6. 安全ロックONでは外部通知しない (test_safety_lock_blocks_notification)
7. Discord送信失敗を成功として扱わない (test_discord_failure_not_treated_as_success)
8. 同時実行で二重通知しない (test_concurrent_execution_prevents_duplicate_alert)
9. Windows再起動後に監視を再開できる (test_resume_monitoring_after_restart)
10. 実データ未接続でも安全に待機する (test_safe_standby_when_unconnected)
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
import numpy as np

from src.automated_monitor import (
    run_monitor_scan,
    init_alert_db,
    is_already_delivered,
    record_alert_delivery,
    load_alert_config,
    save_alert_config,
    validate_feed_payload
)
from scripts.run_background_scan import run_cycle
from scripts.run_background_monitor import run_daemon
from src.prerace_feature_pipeline import (
    build_prerace_28features,
    validate_clean_prerace_input,
    validate_feature_alignment
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class SafetyAndVerificationSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.state_db = self.dir_path / "test_alert_state.db"
        self.config_path = self.dir_path / "test_alert_config.json"
        init_alert_db(self.state_db)

        self.default_config = {
            "min_ev": 1.15,
            "min_win_prob": 0.20,
            "max_odds_age_seconds": 120,
            "min_seconds_to_start": 300,
            "max_seconds_to_start": 1800,
            "windows_toast_enabled": True,
            "discord_enabled": True,
            "safety_lock_enabled": True,
        }
        save_alert_config(self.default_config, self.config_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_valid_feed(self, certified=True, past_odds_sec=30, start_min=15, win_odds=6.0):
        now = datetime.now(timezone.utc)
        return {
            "published_at": (now - timedelta(seconds=10)).isoformat(),
            "certified_source": certified,
            "source": "jra_official" if certified else "unverified_feed",
            "races": [
                {
                    "race_id": "202610260201",
                    "race_date": now.strftime("%Y-%m-%d"),
                    "race_time": (now + timedelta(minutes=start_min)).isoformat(),
                    "race_status": "upcoming",
                    "race_course": "東京",
                    "surface": "芝",
                    "distance": 1600,
                    "condition": "良",
                    "direction": "左",
                    "horses": [
                        {
                            "horse_number": 1,
                            "horse_name": "セーフティワン",
                            "win_odds": win_odds,
                            "odds_observed_at": (now - timedelta(seconds=past_odds_sec)).isoformat(),
                            "age": 4,
                            "weight_carry": 57.0,
                            "sex": "牡",
                            "horse_weight": 500.0,
                            "horse_weight_diff": 0.0
                        }
                    ]
                }
            ]
        }

    # 1. 不正なJSON・スキーマ・時刻を拒絶する
    def test_reject_invalid_feed_json(self):
        now = datetime.now(timezone.utc)
        # ケースA: 壊れたスキーマ（リストではなく辞書以外）
        ok, reason, _, _ = validate_feed_payload("invalid string payload", now)
        self.assertFalse(ok)
        self.assertIn("dictionary", reason)

        # ケースB: 未来の公開日時 (published_at > now)
        future_feed = {"published_at": (now + timedelta(minutes=5)).isoformat(), "races": []}
        ok, reason, _, _ = validate_feed_payload(future_feed, now)
        self.assertFalse(ok)
        self.assertIn("Future publication", reason)

        # ケースC: 重複馬番
        dup_horse_feed = self._make_valid_feed()
        dup_horse_feed["races"][0]["horses"].append({
            "horse_number": 1,  # 重複
            "horse_name": "重複馬",
            "win_odds": 3.0
        })
        ok, reason, _, _ = validate_feed_payload(dup_horse_feed, now)
        self.assertFalse(ok)
        self.assertIn("duplicate horse_number", reason)

    # 2. 出所未確認データでは通知しない
    def test_unconfirmed_source_blocks_notification(self):
        # certified_source: False のフィード
        feed_data = self._make_valid_feed(certified=False, win_odds=10.0)
        feed_file = self.dir_path / "unconfirmed_feed.json"
        feed_file.write_text(json.dumps(feed_data, ensure_ascii=False), encoding="utf-8")

        # 設定側で安全ロックを解除しようとしても、出所未確認なら強制ロックされること
        cfg = load_alert_config(self.config_path)
        cfg["safety_lock_enabled"] = False
        save_alert_config(cfg, self.config_path)

        with patch("src.notifier.send_windows_notification") as mock_toast:
            res = run_monitor_scan(feed_path=feed_file, state_db_path=self.state_db, config_path=self.config_path)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertEqual(res["notifications_sent"], 0)
            self.assertTrue(res["safety_lock_enabled"])
            self.assertFalse(res["is_certified_source"])
            mock_toast.assert_not_called()

    # 3. 未来の成績が予測結果に混入しない
    def test_prevent_future_leakage_in_past_stats(self):
        # 過去DBのシミュレーション: 対象日より前の成績と、対象日以降（未来）の成績を格納
        test_db = self.dir_path / "test_keiba.db"
        with closing(sqlite3.connect(test_db)) as conn, conn:
            conn.execute("""
            CREATE TABLE races (
                race_id TEXT PRIMARY KEY,
                date TEXT,
                race_course TEXT,
                distance REAL,
                condition TEXT
            );
            """)
            conn.execute("""
            CREATE TABLE race_results (
                race_id TEXT,
                horse_id TEXT,
                jockey_id TEXT,
                finish_order INTEGER,
                last_3f REAL
            );
            """)
            # 2025-05-01 (過去レース: 1着)
            conn.execute("INSERT INTO races VALUES ('R1', '2025-05-01', '東京', 1600, '良')")
            conn.execute("INSERT INTO race_results VALUES ('R1', 'H001', 'J001', 1, 34.0)")
            # 2025-05-10 (未来レース: 10着 - 混入してはならない)
            conn.execute("INSERT INTO races VALUES ('R2', '2025-05-10', '東京', 1600, '良')")
            conn.execute("INSERT INTO race_results VALUES ('R2', 'H001', 'J001', 10, 38.0)")

        # ターゲット日を 2025-05-05 とした場合、R1(過去)のみが反映され、R2(未来)は集計に含まれない
        race_info = {
            "race_id": "TARGET_RACE",
            "race_date": "2025-05-05",
            "race_course": "東京",
            "surface": "芝",
            "distance": 1600
        }
        horses = [{
            "horse_number": 1,
            "horse_id": "H001",
            "jockey_id": "J001",
            "age": 4,
            "weight_carry": 56.0,
            "horse_weight": 480.0
        }]
        feat_df = build_prerace_28features(race_info, horses, past_db_path=test_db, target_date="2025-05-05")
        # past_race_count は 1（R1のみ）、prev_rank は 1.0 であること（R2の10着は無視）
        self.assertEqual(feat_df.loc[0, "past_race_count"], 1)
        self.assertEqual(feat_df.loc[0, "prev_rank"], 1.0)
        self.assertEqual(feat_df.loc[0, "jockey_win_rate"], 1.0)

    # 4. 対象日（開催日）がない場合は推論しない
    def test_missing_race_date_stops_inference(self):
        race_info_no_date = {
            "race_id": "RACE_NO_DATE",
            "race_course": "東京",
            "surface": "芝",
            "distance": 1600
        }
        horses = [{"horse_number": 1}]
        with self.assertRaises(ValueError) as ctx:
            build_prerace_28features(race_info_no_date, horses, target_date=None)
        self.assertIn("race_date is required", str(ctx.exception))

    # 5. 必須特徴量の欠損時は推論しない
    def test_missing_required_features_stops_inference(self):
        # 距離 distance が欠落
        race_info_no_dist = {
            "race_id": "RACE_NO_DIST",
            "race_date": "2026-10-26",
            "race_course": "東京",
            "surface": "芝"
        }
        horses = [{"horse_number": 1}]
        with self.assertRaises(ValueError) as ctx:
            build_prerace_28features(race_info_no_dist, horses)
        self.assertIn("INSUFFICIENT_FEATURES", str(ctx.exception))

    # 6. 安全ロックONでは外部通知しない
    def test_safety_lock_blocks_notification(self):
        feed_data = self._make_valid_feed(certified=True, win_odds=10.0)
        feed_file = self.dir_path / "valid_feed.json"
        feed_file.write_text(json.dumps(feed_data, ensure_ascii=False), encoding="utf-8")

        # safety_lock_enabled: True (デフォルト)
        cfg = load_alert_config(self.config_path)
        cfg["safety_lock_enabled"] = True
        save_alert_config(cfg, self.config_path)

        with patch("src.notifier.send_windows_notification") as mock_toast:
            res = run_monitor_scan(feed_path=feed_file, state_db_path=self.state_db, config_path=self.config_path)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertEqual(res["notifications_sent"], 0)
            mock_toast.assert_not_called()

        with closing(sqlite3.connect(f"file:{self.state_db.resolve()}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT delivery_status, windows_status FROM alert_delivery").fetchone()
            self.assertEqual(row[0], "SKIPPED_BY_SAFETY_LOCK")
            self.assertEqual(row[1], "BLOCKED_BY_SAFETY_LOCK")

    # 7. Discord送信失敗を成功として扱わない
    def test_discord_failure_not_treated_as_success(self):
        feed_data = self._make_valid_feed(certified=True, win_odds=10.0)
        feed_file = self.dir_path / "valid_feed_discord.json"
        feed_file.write_text(json.dumps(feed_data, ensure_ascii=False), encoding="utf-8")

        cfg = load_alert_config(self.config_path)
        cfg["safety_lock_enabled"] = False
        cfg["discord_enabled"] = True
        cfg["windows_toast_enabled"] = False  # Windows通知はOFFにしてDiscordのみ検証
        save_alert_config(cfg, self.config_path)

        with patch("src.notifier.get_discord_webhook", return_value="https://discord.com/api/webhooks/fail"), \
             patch("requests.post", side_effect=Exception("HTTP 500 Internal Error")):
            res = run_monitor_scan(feed_path=feed_file, state_db_path=self.state_db, config_path=self.config_path)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertEqual(res["notifications_sent"], 0)

        with closing(sqlite3.connect(f"file:{self.state_db.resolve()}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT delivery_status, discord_status FROM alert_delivery").fetchone()
            self.assertEqual(row[0], "FAILED")
            self.assertEqual(row[1], "FAILED")

    # 8. 同時実行で二重通知しない
    def test_concurrent_execution_prevents_duplicate_alert(self):
        feed_data = self._make_valid_feed(certified=True, win_odds=10.0)
        feed_file = self.dir_path / "concurrent_feed.json"
        feed_file.write_text(json.dumps(feed_data, ensure_ascii=False), encoding="utf-8")

        cfg = load_alert_config(self.config_path)
        cfg["safety_lock_enabled"] = False
        cfg["windows_toast_enabled"] = True
        cfg["discord_enabled"] = False
        save_alert_config(cfg, self.config_path)

        notification_counter = {"count": 0}

        def mock_notify(title, body):
            notification_counter["count"] += 1
            return True

        with patch("src.notifier.send_windows_notification", side_effect=mock_notify):
            # 2つの並行スレッドから同時にスキャンを実行
            def run_scan():
                return run_monitor_scan(feed_path=feed_file, state_db_path=self.state_db, config_path=self.config_path)

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(run_scan)
                f2 = executor.submit(run_scan)
                res1 = f1.result()
                res2 = f2.result()

        # 同時実行されても通知送信は最大1回のみ
        total_sent = res1["notifications_sent"] + res2["notifications_sent"]
        self.assertLessEqual(total_sent, 1)

    # 9. Windows再起動後に監視を再開できる
    def test_resume_monitoring_after_restart(self):
        feed_data = self._make_valid_feed(certified=True)
        feed_file = self.dir_path / "restart_feed.json"
        feed_file.write_text(json.dumps(feed_data, ensure_ascii=False), encoding="utf-8")

        status_file = self.dir_path / "daemon_status_restart.json"
        pid_file = self.dir_path / "daemon_restart.pid"
        lock_file = self.dir_path / "daemon_restart.lock"

        # PC強制終了シミュレーション（孤立した旧PIDファイルが存在）
        pid_file.write_text("9999999", encoding="utf-8")

        with patch("scripts.run_background_monitor.STATUS_PATH", status_file), \
             patch("scripts.run_background_monitor.PID_PATH", pid_file), \
             patch("scripts.run_background_monitor.LOCK_PATH", lock_file):
            run_daemon(manifest_path=str(feed_file), db_path=str(self.state_db), interval_seconds=1, max_cycles=1)

        self.assertTrue(status_file.exists())
        st = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(st["total_cycles"], 1)

    # 10. 実データ未接続でも安全に待機する
    def test_safe_standby_when_unconnected(self):
        non_existent_feed = self.dir_path / "unconnected_feed.json"
        res = run_monitor_scan(feed_path=non_existent_feed, state_db_path=self.state_db, config_path=self.config_path)
        self.assertEqual(res["status"], "NO_FEED_FILE")
        self.assertFalse(res["model_inference_allowed"])
        self.assertEqual(res["notifications_sent"], 0)
        self.assertEqual(res["processed_races"], 0)

    # 11. 学習時と推論時の28特徴量の定義・順序・型・カテゴリ完全一致検証
    def test_feature_28_alignment_exact_match(self):
        from src.features import FEATURE_COLS_V2_28, CATEGORICAL_COLS
        from src.model import KeibaAIWinModel, MODEL_WIN_V2_28_PATH
        race_info = {
            "race_id": "TEST_ALIGN",
            "race_date": "2026-10-26",
            "race_course": "東京",
            "surface": "芝",
            "distance": 2000,
            "condition": "良"
        }
        horses = [{
            "horse_number": 1,
            "age": 4.0,
            "weight_carry": 56.0,
            "sex": "牡",
            "horse_weight": 480.0,
            "running_style": "先行"
        }]
        df = build_prerace_28features(race_info, horses)
        validate_feature_alignment(df)
        self.assertEqual(len(FEATURE_COLS_V2_28), 28)
        for col in FEATURE_COLS_V2_28:
            self.assertIn(col, df.columns)

        # モデルの期待する28特徴量と完全一致すること
        win_model = KeibaAIWinModel(model_path=MODEL_WIN_V2_28_PATH)
        win_model.load()
        preds = win_model.predict_win_race(df)
        self.assertEqual(len(preds), 1)
        self.assertGreaterEqual(preds.loc[0, "pred_win_prob"], 0.0)

    # 12. テスト通知と本番推奨通知の分離確認
    def test_test_notification_separated_from_real_alerts(self):
        from src.notifier import send_windows_notification
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            # テスト通知はタイトルに [TEST] 等が明示され、実オッズ・推論ロジックとは完全に分離
            send_windows_notification("【KEIBA AI テスト通知】", "テストメッセージです")
            mock_popen.assert_called_once()
            call_kwargs = mock_popen.call_args[1]
            env = call_kwargs.get("env", {})
            self.assertEqual(env.get("KEIBA_ALERT_TITLE"), "【KEIBA AI テスト通知】")

    # 13. 架空補完の禁止（age欠損時は推論停止）
    def test_fabricated_defaults_forbidden(self):
        race_info = {
            "race_id": "NO_DEFAULTS",
            "race_date": "2026-10-26",
            "race_course": "東京",
            "surface": "芝",
            "distance": 2000
        }
        # age が None（未取得）の場合、4歳で捏造補完せず推論停止すること
        horses_missing_age = [{
            "horse_number": 1,
            "weight_carry": 56.0
        }]
        with self.assertRaises(ValueError) as ctx:
            build_prerace_28features(race_info, horses_missing_age)
        self.assertIn("Missing required horse attribute 'age'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
