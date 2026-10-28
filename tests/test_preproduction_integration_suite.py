"""
第3回本番運用前 統合検証テストスイート (Pre-production Integration Suite)

【検証項目】
1. 自己申告データ認証の偽装遮断 (test_spoofed_certification_rejected)
2. 正規取得プロセス経由の真正性照合 (test_verified_provenance_flow)
3. 最新スナップショットの自動解決と接続 (test_resolve_latest_snapshot_connection)
4. 期限切れスナップショットの安全停止 (test_expired_snapshot_stops_safely)
5. JV-Link生データから28特徴量・推論への変換結合 (test_jvlink_to_28features_pipeline)
6. 安全ロック解除後の正常再評価と通知 (test_safety_lock_unlocked_allows_real_alert)
7. 通信失敗時の安全再試行とリトライ上限 (test_retry_failure_and_max_retries)
8. E2E 全体パイプライン（取得〜検証〜推論〜EV〜通知〜保存） (test_e2e_preproduction_pipeline)
"""
import hashlib
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
    save_alert_config,
    resolve_latest_authorized_feed
)
from scripts.run_background_scan import run_cycle
from src.prerace_feature_pipeline import build_prerace_28features, validate_feature_alignment
from src.provenance_verifier import verify_feed_provenance
from src.jvlink_adapter import (
    parse_jv_ra_record,
    parse_jv_se_record,
    parse_jv_o1_record,
    convert_jvlink_records_to_prerace_feed
)
from src.model import KeibaAIWinModel, MODEL_WIN_V2_28_PATH

REPO_ROOT = Path(__file__).resolve().parent.parent


class PreproductionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.state_db = self.dir_path / "preprod_state.db"
        self.config_path = self.dir_path / "alert_config.json"
        self.provider_config = self.dir_path / "provider_config.json"
        self.feed_dir = self.dir_path / "authorized_feed"
        self.feed_dir.mkdir(parents=True, exist_ok=True)
        init_alert_db(self.state_db)

        self.default_cfg = {
            "min_ev": 1.15,
            "min_win_prob": 0.20,
            "max_odds_age_seconds": 120,
            "min_seconds_to_start": 300,
            "max_seconds_to_start": 1800,
            "windows_toast_enabled": True,
            "discord_enabled": True,
            "safety_lock_enabled": False,  # テスト時に認証状態と連動させる
            "max_retries": 3,
        }
        save_alert_config(self.default_cfg, self.config_path)

        # プロバイダ設定
        self.prov_cfg = {
            "jra_van_authorized": False,
            "licensed_feed_enabled": True,
            "active_provider": "LICENSED_FEED"
        }
        self.provider_config.write_text(json.dumps(self.prov_cfg), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_raw_feed(self, win_odds=6.0, start_min=15, past_odds_sec=30):
        now = datetime.now(timezone.utc)
        return {
            "published_at": (now - timedelta(seconds=10)).isoformat(),
            "races": [
                {
                    "race_id": "202610260511",
                    "race_date": now.strftime("%Y-%m-%d"),
                    "race_time": (now + timedelta(minutes=start_min)).isoformat(),
                    "race_status": "upcoming",
                    "race_course": "東京",
                    "surface": "芝",
                    "distance": 2000,
                    "condition": "良",
                    "direction": "左",
                    "horses": [
                        {
                            "horse_number": 1,
                            "horse_name": "リアルプロミス",
                            "age": 4.0,
                            "weight_carry": 57.0,
                            "sex": "牡",
                            "horse_weight": 490.0,
                            "horse_weight_diff": 0.0,
                            "win_odds": win_odds,
                            "odds_observed_at": (now - timedelta(seconds=past_odds_sec)).isoformat()
                        }
                    ]
                }
            ]
        }

    # 1. 自己申告データ認証の偽装遮断
    def test_spoofed_certification_rejected(self):
        # 外部JSONが "certified_source": true や "source": "jra_van" と自己申告している
        spoofed_payload = self._create_raw_feed(win_odds=10.0)
        spoofed_payload["certified_source"] = True
        spoofed_payload["source"] = "jra_van"

        spoofed_file = self.dir_path / "spoofed.json"
        spoofed_file.write_text(json.dumps(spoofed_payload), encoding="utf-8")

        # 真正性検証: システムのプロバイダ設定やアーカイブ照合がないため弾かれる
        is_cert, reason = verify_feed_provenance(spoofed_file, spoofed_payload, self.provider_config, self.feed_dir)
        self.assertFalse(is_cert)
        self.assertIn("PROVENANCE_REJECTED", reason)

        # スキャン実行時にも強制的に安全ロックが働き、外部通知は0件
        with patch("src.notifier.send_windows_notification") as mock_toast:
            res = run_monitor_scan(
                feed_path=spoofed_file,
                state_db_path=self.state_db,
                config_path=self.config_path,
                provider_config_path=self.provider_config
            )
            self.assertEqual(res["status"], "COMPLETED")
            self.assertEqual(res["notifications_sent"], 0)
            self.assertFalse(res["is_certified_source"])
            self.assertTrue(res["safety_lock_enabled"])
            mock_toast.assert_not_called()

    # 2. 正規取得プロセス経由の真正性照合
    def test_verified_provenance_flow(self):
        feed_data = self._create_raw_feed()
        now_iso = datetime.now(timezone.utc).isoformat()
        snap_dir = self.feed_dir / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        archive_file = snap_dir / "snapshot_001.json"
        archive_payload = {"received_at": now_iso, "feed": feed_data}
        archive_file.write_text(json.dumps(archive_payload, ensure_ascii=False), encoding="utf-8")

        # ステータスメタデータ生成
        status_file = self.feed_dir / "last_feed_status.json"
        status_payload = {
            "status": "VALIDATED_CERTIFIED",
            "received_at": now_iso,
            "archive": str(archive_file)
        }
        status_file.write_text(json.dumps(status_payload), encoding="utf-8")

        # 真正性セッション署名を発行
        from src.provenance_verifier import create_acquisition_signature
        create_acquisition_signature(
            feed_payload=feed_data,
            feed_path=archive_file,
            module_name="tests.test_preproduction_integration_suite",
            provider="licensed_api",
            output_path=self.feed_dir / ".acquisition_session.json",
            test_mode=True
        )

        # 照合 (テストモード有効)
        is_cert, reason = verify_feed_provenance(archive_file, feed_data, self.provider_config, self.feed_dir, test_mode=True)
        self.assertTrue(is_cert)
        self.assertEqual(reason, "PROVENANCE_VERIFIED")

    # 3. 最新スナップショットの自動解決と接続
    def test_resolve_latest_snapshot_connection(self):
        feed_data = self._create_raw_feed()
        now_iso = datetime.now(timezone.utc).isoformat()
        archive_file = self.feed_dir / "snapshots" / "snap_current.json"
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        archive_file.write_text(json.dumps({"received_at": now_iso, "feed": feed_data}), encoding="utf-8")

        status_file = self.feed_dir / "last_feed_status.json"
        status_file.write_text(json.dumps({
            "status": "VALIDATED_CERTIFIED",
            "received_at": now_iso,
            "archive": str(archive_file)
        }), encoding="utf-8")

        resolved_path, reason = resolve_latest_authorized_feed(self.feed_dir)
        self.assertEqual(resolved_path.resolve(), archive_file.resolve())
        self.assertEqual(reason, "RESOLVED_FROM_STATUS")

    # 4. 期限切れスナップショットの安全停止
    def test_expired_snapshot_stops_safely(self):
        feed_data = self._create_raw_feed()
        # 2時間前の古いデータ
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        archive_file = self.feed_dir / "snapshots" / "snap_old.json"
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        archive_file.write_text(json.dumps({"received_at": old_iso, "feed": feed_data}), encoding="utf-8")

        status_file = self.feed_dir / "last_feed_status.json"
        status_file.write_text(json.dumps({
            "status": "VALIDATED_CERTIFIED",
            "received_at": old_iso,
            "archive": str(archive_file)
        }), encoding="utf-8")

        resolved_path, reason = resolve_latest_authorized_feed(self.feed_dir)
        self.assertIsNone(resolved_path)
        self.assertEqual(reason, "FEED_EXPIRED")

    # 5. JV-Link生データから28特徴量・推論への変換結合
    def test_jvlink_to_28features_pipeline(self):
        race_key = "2026102605010101"

        def _rec(kind, size, race=race_key, kubun="2", created="20261026"):
            record = bytearray(b" " * (size - 2) + b"\r\n")
            record[:2] = kind.encode("ascii")
            record[2:3] = kubun.encode("ascii")
            record[3:11] = created.encode("ascii")
            record[11:27] = race.encode("ascii")
            return record

        # 公式仕様準拠 RA (1272バイト)
        ra_raw = _rec("RA", 1272)
        ra_raw[697:701] = b"1600"  # 距離
        ra_raw[705:707] = b"11"    # トラックコード (東京・芝)
        ra_raw[873:877] = b"1500"  # 発走時刻
        ra_raw[881:883] = b"01"    # 出走頭数
        ra_raw[888:890] = b"00"    # 馬場状態 (良相当)
        ra_line = bytes(ra_raw)

        # 公式仕様準拠 SE (555バイト)
        se_raw = _rec("SE", 555)
        se_raw[27:28] = b"1"       # 枠番
        se_raw[28:30] = b"01"      # 馬番
        se_raw[30:40] = b"2022100001"
        se_raw[40:76] = "テストホース".encode("cp932").ljust(36, b" ")
        se_raw[78:79] = b"1"       # 牡
        se_raw[82:84] = b"04"       # 4歳
        se_raw[288:291] = b"560"   # 斤量 56.0kg
        se_raw[296:301] = b"01234" # 騎手コード
        se_raw[324:327] = b"480"   # 馬体重
        se_raw[328:331] = b"+02"   # 増減
        se_line = bytes(se_raw)

        # 公式仕様準拠 O1 (962バイト)
        o1_raw = _rec("O1", 962, kubun="1")
        o1_raw[27:35] = b"10261325"
        o1_raw[35:37] = b"01"      # 登録頭数
        o1_raw[37:39] = b"01"      # 出走頭数
        o1_raw[39:40] = b"7"       # 単勝発売中
        o1_raw[40:41] = b"7"
        o1_raw[42:43] = b"3"
        o1_raw[43:51] = b"01005201" # 馬番1, 5.2倍, 1番人気
        o1_line = bytes(o1_raw)

        feed_dict = convert_jvlink_records_to_prerace_feed([ra_line, se_line, o1_line])
        self.assertEqual(feed_dict["source"], "jvlink")
        self.assertEqual(len(feed_dict["races"]), 1)
        race = feed_dict["races"][0]
        self.assertEqual(race["race_course"], "東京")
        self.assertEqual(race["distance"], 1600.0)
        self.assertEqual(race["surface"], "芝")

        # 28特徴量パイプラインへ接続
        feat_df = build_prerace_28features(race, race["horses"])
        validate_feature_alignment(feat_df)
        self.assertEqual(len(feat_df), 1)

        # モデル推論
        win_model = KeibaAIWinModel(model_path=MODEL_WIN_V2_28_PATH)
        win_model.load()
        preds = win_model.predict_win_race(feat_df)
        self.assertEqual(len(preds), 1)
        self.assertGreater(preds.loc[0, "pred_win_prob"], 0.0)

    # 6. 安全ロック解除後の正常再評価と通知
    def test_safety_lock_unlocked_allows_real_alert(self):
        feed_data = self._create_raw_feed(win_odds=10.0)
        feed_file = self.dir_path / "feed_unlock.json"
        feed_file.write_text(json.dumps(feed_data), encoding="utf-8")

        # サイクル1: 安全ロック中
        cfg = load_alert_config(self.config_path)
        cfg["safety_lock_enabled"] = True
        save_alert_config(cfg, self.config_path)

        res1 = run_monitor_scan(feed_path=feed_file, state_db_path=self.state_db, config_path=self.config_path)
        self.assertEqual(res1["notifications_sent"], 0)

        with closing(sqlite3.connect(f"file:{self.state_db.resolve()}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT delivery_status FROM alert_delivery").fetchone()
            self.assertEqual(row[0], "SKIPPED_BY_SAFETY_LOCK")

        # サイクル2: 安全ロック解除＆正規認証
        cfg["safety_lock_enabled"] = False
        save_alert_config(cfg, self.config_path)

        with patch("src.automated_monitor.verify_feed_provenance", return_value=(True, "PROVENANCE_VERIFIED")), \
             patch("src.automated_monitor.send_windows_notification", return_value=True) as mock_toast:
            res2 = run_monitor_scan(feed_path=feed_file, state_db_path=self.state_db, config_path=self.config_path)
            # 安全ロック中にスキップされたものが、ロック解除後に再評価され正常に送信されること
            self.assertEqual(res2["notifications_sent"], 1)
            mock_toast.assert_called_once()

        with closing(sqlite3.connect(f"file:{self.state_db.resolve()}?mode=ro", uri=True)) as conn:
            row2 = conn.execute("SELECT delivery_status, windows_status FROM alert_delivery").fetchone()
            self.assertEqual(row2[0], "DELIVERED")
            self.assertEqual(row2[1], "DELIVERED")

    # 7. 通信失敗時の安全再試行とリトライ上限
    def test_retry_failure_and_max_retries(self):
        feed_data = self._create_raw_feed(win_odds=10.0)
        feed_file = self.dir_path / "retry_feed.json"
        feed_file.write_text(json.dumps(feed_data), encoding="utf-8")

        cfg = load_alert_config(self.config_path)
        cfg["safety_lock_enabled"] = False
        cfg["windows_toast_enabled"] = False
        cfg["discord_enabled"] = True
        cfg["max_retries"] = 2
        save_alert_config(cfg, self.config_path)

        with patch("src.automated_monitor.verify_feed_provenance", return_value=(True, "PROVENANCE_VERIFIED")), \
             patch("src.notifier.get_discord_webhook", return_value="https://discord.com/api/test"), \
             patch("requests.post", side_effect=Exception("Network down")):

            # 試行 1 (初回失敗: retry_count=1)
            res1 = run_monitor_scan(feed_path=feed_file, state_db_path=self.state_db, config_path=self.config_path)
            self.assertEqual(res1["notifications_sent"], 0)

            # 試行 2 (再試行失敗: retry_count=2)
            res2 = run_monitor_scan(feed_path=feed_file, state_db_path=self.state_db, config_path=self.config_path)
            self.assertEqual(res2["notifications_sent"], 0)

            # 試行 3 (上限到達 retry_count>=2 のため再試行スキップ)
            res3 = run_monitor_scan(feed_path=feed_file, state_db_path=self.state_db, config_path=self.config_path)
            self.assertEqual(res3["notifications_sent"], 0)

        with closing(sqlite3.connect(f"file:{self.state_db.resolve()}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT delivery_status, retry_count FROM alert_delivery").fetchone()
            self.assertEqual(row[0], "FAILED")
            self.assertEqual(row[1], 2)

    # 8. E2E 全体パイプライン
    def test_e2e_preproduction_pipeline(self):
        feed_data = self._create_raw_feed(win_odds=8.5)
        now_iso = datetime.now(timezone.utc).isoformat()
        archive_file = self.feed_dir / "snapshots" / "e2e_snap.json"
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        archive_file.write_text(json.dumps({"received_at": now_iso, "feed": feed_data}), encoding="utf-8")

        status_file = self.feed_dir / "last_feed_status.json"
        status_file.write_text(json.dumps({
            "status": "VALIDATED_CERTIFIED",
            "received_at": now_iso,
            "archive": str(archive_file)
        }), encoding="utf-8")

        cfg = load_alert_config(self.config_path)
        cfg["safety_lock_enabled"] = False
        save_alert_config(cfg, self.config_path)

        with patch("src.automated_monitor.verify_feed_provenance", return_value=(True, "PROVENANCE_VERIFIED")), \
             patch("src.automated_monitor.send_windows_notification", return_value=True), \
             patch("src.notifier.send_windows_notification", return_value=True):
            # run_cycle にディレクトリパスを渡すと最新スナップショットを解決して全処理実行
            res = run_cycle(str(self.feed_dir), str(self.state_db), config_path=self.config_path)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertEqual(res["processed_races"], 1)
            self.assertEqual(res["notifications_sent"], 1)


if __name__ == "__main__":
    unittest.main()
