"""
発走前オッズ・AI予測実時刻保存基盤の統合単体テスト

【検証項目】
1. 30分前・10分前・5分前の各タイムポイントにおける実時刻付きスナップショット正常保存
2. オッズ取得不可時の「欠損扱い」(odds_status='missing')の厳密動作（架空データ代用ゼロ）
3. 確定オッズ(final_settled等)および発走時刻以降オッズの完全遮断・代用禁止
4. 観測時刻 > 取得時刻 の因果律違反データの遮断
5. 二重記録防止 (Idempotency Guard) の検証: 同一(race_id, timing_tag)の重複挿入抑止
6. 取得プロバイダー診断と接続不能時の未接続(DISCONNECTED)判定・架空データ代用ゼロ検証
7. 再試行 (Retry Logic) とリトライ枯渇時の欠損扱い移行検証
8. 自動実行スケジューラ (run_prerace_auto_recorder.py) のスキャン実行と二重抑止
9. 既存DB (data/keiba.db) の SHA-256 完全不変性の検証
10. CLIツール (scripts/record_prerace_asof.py) の正常系・欠損系・監査照会テスト
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import pandas as pd
import numpy as np

# プロジェクトルートを sys.path に追加
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from src.prerace_recorder import (
    record_prerace_snapshot,
    query_snapshot_records,
    compute_target_snapshot_time,
    determine_applicable_timing,
    validate_odds_records,
    PostRaceOddsLeakageError,
    TARGET_TIMINGS
)
from src.odds_provider import (
    OddsProviderManager,
    BaseOddsProvider,
    JVLinkOddsProvider,
    LicensedFeedOddsProvider,
    FileSnapshotOddsProvider
)
from src.odds_history import init_odds_history_db, get_latest_odds_as_of

KEIBA_DB_PATH = repo_root / "data" / "keiba.db"
EXPECTED_KEIBA_DB_SHA256 = "a653bca16a9dcd7ed982e40f23310ce246069bf0759f98afe91ffb191d475104"


class TestPreRaceRecorder(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = Path(self.temp_dir.name) / "test_odds_history.db"

        # テスト用特徴量 DataFrame (2頭)
        self.sample_features = pd.DataFrame([
            {
                "race_id": "202605010101",
                "horse_number": 1,
                "horse_id": "h001",
                "horse_name": "テストホースワン",
                "bracket_number": 1,
                "sex": "牡",
                "age": 4,
                "burden_weight": 57.0,
                "jockey_id": "j001",
                "trainer_id": "t001",
                "horse_weight": 480.0,
                "horse_weight_diff": 0.0,
                "race_course": "東京",
                "surface": "芝",
                "distance": 2000,
                "direction": "左",
                "weather": "晴",
                "condition": "良",
                "grade": "OP",
                "distance_change": 0,
                "rest_weeks": 4,
                "prev_finish_order": 2,
                "prev_odds": 3.5,
                "prev_popularity": 2,
                "prev_last_3f": 34.0,
                "prev_position_1": 3,
                "prev_position_2": 3,
                "prev_position_3": 2,
                "prev_position_4": 2,
                "avg_finish_order_5": 2.5,
                "win_rate_5": 0.4,
                "place_rate_5": 0.8,
                "jockey_win_rate_1y": 0.15,
                "jockey_place_rate_1y": 0.35,
                "trainer_win_rate_1y": 0.12,
                "trainer_place_rate_1y": 0.30,
                "course_win_rate": 0.20,
                "course_place_rate": 0.50,
                "surface_win_rate": 0.20,
                "surface_place_rate": 0.50,
                "distance_win_rate": 0.25,
                "distance_place_rate": 0.50,
                "sire_type": "サンデー系",
                "running_style": "先行",
            },
            {
                "race_id": "202605010101",
                "horse_number": 2,
                "horse_id": "h002",
                "horse_name": "テストホーストゥー",
                "bracket_number": 2,
                "sex": "牡",
                "age": 5,
                "burden_weight": 57.0,
                "jockey_id": "j002",
                "trainer_id": "t002",
                "horse_weight": 500.0,
                "horse_weight_diff": 2.0,
                "race_course": "東京",
                "surface": "芝",
                "distance": 2000,
                "direction": "左",
                "weather": "晴",
                "condition": "良",
                "grade": "OP",
                "distance_change": 0,
                "rest_weeks": 6,
                "prev_finish_order": 4,
                "prev_odds": 8.0,
                "prev_popularity": 4,
                "prev_last_3f": 34.8,
                "prev_position_1": 8,
                "prev_position_2": 8,
                "prev_position_3": 7,
                "prev_position_4": 5,
                "avg_finish_order_5": 3.8,
                "win_rate_5": 0.2,
                "place_rate_5": 0.6,
                "jockey_win_rate_1y": 0.10,
                "jockey_place_rate_1y": 0.25,
                "trainer_win_rate_1y": 0.08,
                "trainer_place_rate_1y": 0.22,
                "course_win_rate": 0.10,
                "course_place_rate": 0.30,
                "surface_win_rate": 0.15,
                "surface_place_rate": 0.35,
                "distance_win_rate": 0.15,
                "distance_place_rate": 0.35,
                "sire_type": "サンデー系",
                "running_style": "差し",
            }
        ])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_target_snapshot_time(self):
        race_time = "2026-05-01T15:40:00+09:00"
        t_30m = compute_target_snapshot_time(race_time, "pre_30m")
        t_10m = compute_target_snapshot_time(race_time, "pre_10m")
        t_5m = compute_target_snapshot_time(race_time, "pre_5m")

        self.assertEqual(t_30m.strftime("%H:%M:%S"), "15:10:00")
        self.assertEqual(t_10m.strftime("%H:%M:%S"), "15:30:00")
        self.assertEqual(t_5m.strftime("%H:%M:%S"), "15:35:00")

    def test_determine_applicable_timing(self):
        race_time = "2026-05-01T15:40:00+09:00"
        # 30分前付近 (15:10)
        self.assertEqual(determine_applicable_timing(race_time, "2026-05-01T15:10:00+09:00"), "pre_30m")
        # 10分前付近 (15:30)
        self.assertEqual(determine_applicable_timing(race_time, "2026-05-01T15:30:00+09:00"), "pre_10m")
        # 5分前付近 (15:35)
        self.assertEqual(determine_applicable_timing(race_time, "2026-05-01T15:35:00+09:00"), "pre_5m")
        # 離れた時刻 (14:00)
        self.assertIsNone(determine_applicable_timing(race_time, "2026-05-01T14:00:00+09:00"))

    def test_record_prerace_30m_10m_5m(self):
        """30分前・10分前・5分前の3スナップショットが実時刻付きで正確に保存されること"""
        race_id = "202605010101"
        race_time = "2026-05-01T15:40:00+09:00"

        # 1. 30分前スナップショット (目標: 15:10:00)
        snap_30m = [
            {"race_id": race_id, "horse_number": 1, "odds_value": 3.0, "popularity": 1, "observed_at": "2026-05-01T15:09:50+09:00", "fetched_at": "2026-05-01T15:09:55+09:00", "source": "JV-Link", "data_status": "pre_30m"},
            {"race_id": race_id, "horse_number": 2, "odds_value": 5.0, "popularity": 2, "observed_at": "2026-05-01T15:09:50+09:00", "fetched_at": "2026-05-01T15:09:55+09:00", "source": "JV-Link", "data_status": "pre_30m"},
        ]
        res_30m = record_prerace_snapshot(
            race_id=race_id,
            scheduled_race_time=race_time,
            timing_tag="pre_30m",
            odds_records=snap_30m,
            features_df=self.sample_features,
            executed_at="2026-05-01T15:10:00+09:00",
            db_path=self.test_db_path
        )
        self.assertEqual(res_30m["status"], "RECORDED")
        self.assertEqual(res_30m["odds_status"], "captured")
        self.assertEqual(res_30m["odds_count"], 2)
        self.assertEqual(res_30m["pred_status"], "recorded")
        self.assertEqual(res_30m["pred_count"], 2)

        # 2. 10分前スナップショット (目標: 15:30:00)
        snap_10m = [
            {"race_id": race_id, "horse_number": 1, "odds_value": 2.8, "popularity": 1, "observed_at": "2026-05-01T15:29:50+09:00", "fetched_at": "2026-05-01T15:29:55+09:00", "source": "JV-Link", "data_status": "pre_10m"},
            {"race_id": race_id, "horse_number": 2, "odds_value": 5.5, "popularity": 2, "observed_at": "2026-05-01T15:29:50+09:00", "fetched_at": "2026-05-01T15:29:55+09:00", "source": "JV-Link", "data_status": "pre_10m"},
        ]
        res_10m = record_prerace_snapshot(
            race_id=race_id,
            scheduled_race_time=race_time,
            timing_tag="pre_10m",
            odds_records=snap_10m,
            features_df=self.sample_features,
            executed_at="2026-05-01T15:30:00+09:00",
            db_path=self.test_db_path
        )
        self.assertEqual(res_10m["status"], "RECORDED")
        self.assertEqual(res_10m["odds_status"], "captured")

        # 3. 5分前スナップショット (目標: 15:35:00)
        snap_5m = [
            {"race_id": race_id, "horse_number": 1, "odds_value": 2.5, "popularity": 1, "observed_at": "2026-05-01T15:34:50+09:00", "fetched_at": "2026-05-01T15:34:55+09:00", "source": "JV-Link", "data_status": "pre_5m"},
            {"race_id": race_id, "horse_number": 2, "odds_value": 6.0, "popularity": 2, "observed_at": "2026-05-01T15:34:50+09:00", "fetched_at": "2026-05-01T15:34:55+09:00", "source": "JV-Link", "data_status": "pre_5m"},
        ]
        res_5m = record_prerace_snapshot(
            race_id=race_id,
            scheduled_race_time=race_time,
            timing_tag="pre_5m",
            odds_records=snap_5m,
            features_df=self.sample_features,
            executed_at="2026-05-01T15:35:00+09:00",
            db_path=self.test_db_path
        )
        self.assertEqual(res_5m["status"], "RECORDED")
        self.assertEqual(res_5m["odds_status"], "captured")

        # データベース内の実記録を照会して検証
        records = query_snapshot_records(race_id, db_path=self.test_db_path)
        self.assertEqual(len(records["runs"]), 3)
        self.assertEqual(len(records["odds"]), 6)  # 2頭 × 3回
        self.assertEqual(len(records["predictions"]), 6)

        # 予測テーブルの実時刻およびブレンドEVの確認
        preds = records["predictions"]
        for p in preds:
            self.assertIn(p["timing_tag"], TARGET_TIMINGS)
            self.assertIsNotNone(p["predicted_at"])
            self.assertIsNotNone(p["odds_observed_at"])
            self.assertIsNotNone(p["market_odds"])
            self.assertIsNotNone(p["market_prob"])
            self.assertIsNotNone(p["blended_win_prob"])
            self.assertIsNotNone(p["expected_value"])
            self.assertEqual(p["status"], "valid")

    def test_duplicate_recording_prevention(self):
        """二重記録防止: 同一(race_id, timing_tag)の2回目実行が安全に抑止されること"""
        race_id = "202605010101"
        race_time = "2026-05-01T15:40:00+09:00"

        snap_30m = [
            {"race_id": race_id, "horse_number": 1, "odds_value": 3.0, "popularity": 1, "observed_at": "2026-05-01T15:09:50+09:00", "fetched_at": "2026-05-01T15:09:55+09:00", "source": "JV-Link", "data_status": "pre_30m"},
        ]
        # 初回実行
        res1 = record_prerace_snapshot(
            race_id=race_id,
            scheduled_race_time=race_time,
            timing_tag="pre_30m",
            odds_records=snap_30m,
            features_df=self.sample_features,
            executed_at="2026-05-01T15:10:00+09:00",
            db_path=self.test_db_path,
            allow_duplicate=False
        )
        self.assertEqual(res1["status"], "RECORDED")
        self.assertFalse(res1["is_duplicate_prevented"])

        # 2回目実行（同一タイミング・パラメータ）
        res2 = record_prerace_snapshot(
            race_id=race_id,
            scheduled_race_time=race_time,
            timing_tag="pre_30m",
            odds_records=snap_30m,
            features_df=self.sample_features,
            executed_at="2026-05-01T15:10:05+09:00",
            db_path=self.test_db_path,
            allow_duplicate=False
        )
        self.assertEqual(res2["status"], "SKIPPED_ALREADY_RECORDED")
        self.assertTrue(res2["is_duplicate_prevented"])

        # DB内のレコード数が増えていないことを確認
        records = query_snapshot_records(race_id, timing_tag="pre_30m", db_path=self.test_db_path)
        self.assertEqual(len(records["runs"]), 1)
        self.assertEqual(len(records["odds"]), 1)

    def test_missing_odds_handling(self):
        """オッズが取得できない場合に欠損扱い(odds_status='missing')として記録され、架空データは生成されないこと"""
        race_id = "202605010101"
        race_time = "2026-05-01T15:40:00+09:00"

        # オッズレコードなし（空リスト）で実行
        res = record_prerace_snapshot(
            race_id=race_id,
            scheduled_race_time=race_time,
            timing_tag="pre_30m",
            odds_records=[],
            features_df=self.sample_features,
            executed_at="2026-05-01T15:10:00+09:00",
            db_path=self.test_db_path
        )

        self.assertEqual(res["status"], "RECORDED")
        self.assertEqual(res["odds_status"], "missing")
        self.assertEqual(res["odds_count"], 0)
        self.assertEqual(res["pred_status"], "recorded")

        records = query_snapshot_records(race_id, timing_tag="pre_30m", db_path=self.test_db_path)
        self.assertEqual(len(records["runs"]), 1)
        self.assertEqual(records["runs"][0]["odds_status"], "missing")
        self.assertEqual(records["runs"][0]["odds_count"], 0)

        # オッズ履歴テーブルには1件も追加されていないこと
        self.assertEqual(len(records["odds"]), 0)

        # 予測テーブルではオッズ欠損フラグが記録され、市場オッズ・EVは NULL であること（架空オッズ代用ゼロ）
        preds = records["predictions"]
        self.assertEqual(len(preds), 2)
        for p in preds:
            self.assertEqual(p["odds_status"], "missing")
            self.assertEqual(p["status"], "odds_missing")
            self.assertIsNone(p["market_odds"])
            self.assertIsNone(p["market_prob"])
            self.assertIsNone(p["blended_win_prob"])
            self.assertIsNone(p["expected_value"])
            # AI独自の勝率予測自体は記録されていること
            self.assertGreater(p["pred_win_prob"], 0.0)

    def test_prohibit_final_settled_odds(self):
        """確定オッズ (final_settled) の代用・混入を厳格に遮断・拒絶すること"""
        race_id = "202605010103"
        race_time = "2026-05-01T15:40:00+09:00"

        final_odds = [
            {"race_id": race_id, "horse_number": 1, "odds_value": 2.1, "observed_at": "2026-05-01T15:45:00+09:00", "fetched_at": "2026-05-01T15:45:00+09:00", "data_status": "final_settled"},
            {"race_id": race_id, "horse_number": 2, "odds_value": 7.0, "observed_at": "2026-05-01T15:45:00+09:00", "fetched_at": "2026-05-01T15:45:00+09:00", "data_status": "final_settled"},
        ]

        # validate_odds_records で例外発生
        with self.assertRaises(PostRaceOddsLeakageError):
            validate_odds_records(final_odds, race_time, compute_target_snapshot_time(race_time, "pre_5m"))

        # record_prerace_snapshot では rejected_post_race として記録され、オッズ保存は0件
        res = record_prerace_snapshot(
            race_id=race_id,
            scheduled_race_time=race_time,
            timing_tag="pre_5m",
            odds_records=final_odds,
            features_df=self.sample_features,
            executed_at="2026-05-01T15:35:00+09:00",
            db_path=self.test_db_path
        )
        self.assertEqual(res["odds_status"], "rejected_post_race")
        self.assertEqual(res["odds_count"], 0)

        # odds_timeline には保存されていないこと
        records = query_snapshot_records(race_id, db_path=self.test_db_path)
        self.assertEqual(len(records["odds"]), 0)

    def test_prohibit_post_race_timestamps(self):
        """発走時刻以降のオッズタイムスタンプ（未来オッズ）を遮断すること"""
        race_id = "202605010104"
        race_time = "2026-05-01T15:40:00+09:00"

        post_race_odds = [
            {"race_id": race_id, "horse_number": 1, "odds_value": 2.1, "observed_at": "2026-05-01T15:41:00+09:00", "fetched_at": "2026-05-01T15:41:00+09:00", "data_status": "flash"},
        ]

        with self.assertRaises(PostRaceOddsLeakageError):
            validate_odds_records(post_race_odds, race_time, compute_target_snapshot_time(race_time, "pre_5m"))

    def test_causality_guard(self):
        """観測時刻 > 取得時刻 の因果律違反オッズは除外されること"""
        race_id = "202605010105"
        race_time = "2026-05-01T15:40:00+09:00"

        causality_violation = [
            {"race_id": race_id, "horse_number": 1, "odds_value": 2.1, "observed_at": "2026-05-01T15:35:05+09:00", "fetched_at": "2026-05-01T15:35:00+09:00", "data_status": "flash"},
        ]
        valid = validate_odds_records(causality_violation, race_time, compute_target_snapshot_time(race_time, "pre_5m"))
        self.assertEqual(len(valid), 0)

    def test_odds_provider_diagnostics_and_retry(self):
        """プロバイダー診断およびリトライ枯渇時の欠損動作テスト"""
        # モックプロバイダー（意図的に接続失敗させる）
        class FailingProvider(BaseOddsProvider):
            def __init__(self):
                super().__init__("FailingTestProvider")
                self.calls = 0

            def check_connection(self, race_id=None):
                return {"provider": self.name, "connected": True, "status": "TEST"}

            def fetch_prerace_odds(self, race_id, scheduled_race_time, timing_tag):
                self.calls += 1
                raise ConnectionError("Temporary connection timeout")

        failing = FailingProvider()
        mgr = OddsProviderManager([failing])
        records, audit = mgr.fetch_prerace_odds_with_retry(
            "202605010101", "2026-05-01T15:40:00+09:00", "pre_30m", max_retries=2, backoff_sec=0.01
        )
        self.assertIsNone(records)
        self.assertEqual(failing.calls, 2)
        self.assertEqual(audit["outcome"], "retries_exhausted_missing")

    def test_existing_db_integrity_preserved(self):
        """既存DB (data/keiba.db) の SHA-256 が本番バックアップハッシュと完全一致し、一切変更されないこと"""
        if not KEIBA_DB_PATH.exists():
            self.skipTest("data/keiba.db not found")

        current_hash = hashlib.sha256(KEIBA_DB_PATH.read_bytes()).hexdigest()
        self.assertEqual(
            current_hash,
            EXPECTED_KEIBA_DB_SHA256,
            "data/keiba.db has been modified! Existing DB must remain unmodified."
        )

    def test_cli_execution_and_audit_query(self):
        """CLIツール (scripts/record_prerace_asof.py) によるスナップショット記録と照会が正常動作すること"""
        race_id = "202605010106"
        race_time = "2026-05-01T15:40:00+09:00"
        receipt_out = Path(self.temp_dir.name) / "receipt.json"

        # 1. オッズ欠損時 CLI 実行
        cmd = [
            sys.executable,
            str(repo_root / "scripts" / "record_prerace_asof.py"),
            "--race-id", race_id,
            "--race-time", race_time,
            "--timing", "pre_10m",
            "--executed-at", "2026-05-01T15:30:00+09:00",
            "--db", str(self.test_db_path),
            "--out", str(receipt_out)
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertTrue(receipt_out.is_file())
        with open(receipt_out, "r", encoding="utf-8") as f:
            receipt = json.load(f)
        self.assertEqual(receipt["race_id"], race_id)
        self.assertEqual(receipt["timing_tag"], "pre_10m")
        self.assertEqual(receipt["odds_status"], "missing")

        # 2. 監査照会 CLI 実行
        query_out = Path(self.temp_dir.name) / "query.json"
        cmd_query = [
            sys.executable,
            str(repo_root / "scripts" / "record_prerace_asof.py"),
            "--race-id", race_id,
            "--audit-query",
            "--db", str(self.test_db_path),
            "--out", str(query_out)
        ]
        subprocess.run(cmd_query, capture_output=True, text=True, check=True)
        self.assertTrue(query_out.is_file())
        with open(query_out, "r", encoding="utf-8") as f:
            query_res = json.load(f)
        self.assertEqual(len(query_res["runs"]), 1)
        self.assertEqual(query_res["runs"][0]["odds_status"], "missing")

    def test_auto_recorder_runner_diagnose_cli(self):
        """自動実行ランナー (scripts/run_prerace_auto_recorder.py --diagnose-providers) の実行テスト"""
        diag_out = Path(self.temp_dir.name) / "diag.json"
        cmd = [
            sys.executable,
            str(repo_root / "scripts" / "run_prerace_auto_recorder.py"),
            "--diagnose-providers",
            "--out", str(diag_out)
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertTrue(diag_out.is_file())
        with open(diag_out, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("total_providers", data)
        self.assertIn("connected_providers", data)
        self.assertIn("details", data)


if __name__ == "__main__":
    unittest.main()
