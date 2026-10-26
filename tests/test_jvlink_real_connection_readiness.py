"""
第5回JV-Link実接続準備 & 架空補完完全撤去 総合検証テストスイート
(JV-Link Real Connection Readiness & Strict Provenance Test Suite)

【検証要件】
1. 架空の初期値補完の完全撤去（距離、場、時刻、馬場、年齢、斤量、性別、馬番の欠損・不正時はValueError送出）
2. 不正・破損したJV-Dataレコードの拒絶 (Fail-Closed)
3. レースID・出走馬の重複や衝突の防止
4. 別レースのオッズ混入防止（レースキー照合による不正遮断）
5. 未検証レコード（O2〜O7, HR等）の本番利用禁止
6. 環境変数（KEIBA_JVLINK_CERTIFIED=1等）や設定ファイルの単独改ざんによる認証突破の完全遮断
7. 正規取得処理によるセッション署名（Trusted Acquisition Session Token）の検証と本番分離
8. SDK未導入時の安全停止（例外送出とSETUP_REQUIRED判定）
9. 診断ツール (scripts/check_jvlink_readiness.py) の正常実行と出力判定
10. JV-Link生データから28特徴量・推論・EV計算パイプラインへの正規接続準備
"""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.jvlink_adapter import (
    parse_jv_ra_record,
    parse_jv_se_record,
    parse_jv_o1_record,
    convert_jvlink_records_to_prerace_feed,
    UNVERIFIED_RECORD_TYPES
)
from src.jvlink_client import (
    check_environment_compatibility,
    JVLinkClient,
    JVLinkNotInstalledError,
    JVLinkError
)
from src.provenance_verifier import (
    verify_feed_provenance,
    create_acquisition_signature,
    load_provider_config
)
from src.automated_monitor import run_monitor_scan, init_alert_db, load_alert_config, save_alert_config
from scripts.check_jvlink_readiness import run_full_readiness_check


class JVLinkRealConnectionReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.feed_dir = self.dir_path / "authorized_feed"
        self.feed_dir.mkdir(parents=True, exist_ok=True)
        self.state_db = self.dir_path / "alert_state.db"
        self.config_path = self.dir_path / "alert_config.json"
        self.provider_config = self.dir_path / "provider_config.json"

        init_alert_db(self.state_db)

        # プロバイダ設定
        self.provider_config.write_text(json.dumps({
            "jra_van_authorized": True,
            "licensed_feed_enabled": True,
            "active_provider": "JRA_VAN"
        }), encoding="utf-8")

        # 通知設定
        save_alert_config({
            "safety_lock_enabled": False,
            "windows_toast_enabled": True,
            "discord_enabled": False,
            "min_ev": 1.15,
            "min_win_prob": 0.20,
            "max_odds_age_seconds": 3600,
            "min_seconds_to_start": 300,
            "max_seconds_to_start": 7200
        }, self.config_path)

        self.race_key = "2026102605010101"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _build_valid_ra(self, race_key=None, distance="1600", track="10", cond="1", post_time="1500") -> bytes:
        rkey = race_key or self.race_key
        header = "RA" + " " * 9 + rkey
        padding1 = " " * (271 - len(header))
        padding2 = " " * (344 - 277)
        return (
            header + padding1 +
            track + distance + padding2 +
            post_time + cond + " " * 50
        ).encode("cp932")

    def _build_valid_se(self, race_key=None, horse_num="01", b_num="1", horse_id="2022100001",
                        sex="1", age="04", weight_carry="560", jockey_id="00001") -> bytes:
        rkey = race_key or self.race_key
        return (
            "SE" + " " * 9 + rkey +
            b_num + horse_num + horse_id + "テストホース".ljust(36) + sex + age + " " * 2 + weight_carry + jockey_id + " " * 150
        ).encode("cp932")

    def _build_valid_o1(self, race_key=None, odds_str="0052", obs_time="202610261200") -> bytes:
        rkey = race_key or self.race_key
        o1_padding = " " * (47 - 27)
        return (
            "O1" + " " * 9 + rkey + o1_padding +
            odds_str + "01" + " " * 2 + " " * 200
        ).encode("cp932")

    # -------------------------------------------------------------
    # 1. 架空データの初期値補完の完全撤去
    # -------------------------------------------------------------
    def test_no_fabricated_defaults_in_ra_record(self):
        # 距離欠損（スペースのみ）
        ra_no_dist = self._build_valid_ra(distance="    ")
        with self.assertRaises(ValueError) as ctx:
            parse_jv_ra_record(ra_no_dist)
        self.assertIn("Invalid or missing distance", str(ctx.exception))

        # 競馬場コード不正
        ra_bad_course = self._build_valid_ra(race_key="2026102699010101")
        with self.assertRaises(ValueError) as ctx:
            parse_jv_ra_record(ra_bad_course)
        self.assertIn("Invalid or unverified course code", str(ctx.exception))

        # 発走時刻欠損
        ra_no_time = self._build_valid_ra(post_time="    ")
        with self.assertRaises(ValueError) as ctx:
            parse_jv_ra_record(ra_no_time)
        self.assertIn("Missing or invalid post time", str(ctx.exception))

        # 馬場状態不正
        ra_bad_cond = self._build_valid_ra(cond="9")
        with self.assertRaises(ValueError) as ctx:
            parse_jv_ra_record(ra_bad_cond)
        self.assertIn("Invalid or unverified condition code", str(ctx.exception))

    def test_no_fabricated_defaults_in_se_record(self):
        # 馬齢欠損
        se_no_age = self._build_valid_se(age="  ")
        with self.assertRaises(ValueError) as ctx:
            parse_jv_se_record(se_no_age)
        self.assertIn("Invalid age", str(ctx.exception))

        # 斤量欠損
        se_no_weight = self._build_valid_se(weight_carry="   ")
        with self.assertRaises(ValueError) as ctx:
            parse_jv_se_record(se_no_weight)
        self.assertIn("Invalid weight carry", str(ctx.exception))

        # 性別コード不正
        se_bad_sex = self._build_valid_se(sex="9")
        with self.assertRaises(ValueError) as ctx:
            parse_jv_se_record(se_bad_sex)
        self.assertIn("Invalid sex code", str(ctx.exception))

        # 馬番欠損
        se_no_hnum = self._build_valid_se(horse_num="  ")
        with self.assertRaises(ValueError) as ctx:
            parse_jv_se_record(se_no_hnum)
        self.assertIn("Invalid horse number", str(ctx.exception))

    # -------------------------------------------------------------
    # 2. 不正・破損レコードの拒絶 (Fail-Closed)
    # -------------------------------------------------------------
    def test_corrupted_record_rejection(self):
        with self.assertRaises(ValueError):
            parse_jv_ra_record(b"RA short")
        with self.assertRaises(ValueError):
            parse_jv_se_record(b"SE short")
        with self.assertRaises(ValueError):
            parse_jv_o1_record(b"O1 short")

    # -------------------------------------------------------------
    # 3. レースID重複や出走馬番号重複の防止
    # -------------------------------------------------------------
    def test_duplicate_horse_number_in_same_race_rejected(self):
        ra = self._build_valid_ra()
        se1 = self._build_valid_se(horse_num="01", horse_id="2022100001")
        se2 = self._build_valid_se(horse_num="01", horse_id="2022100002")  # 同一番号
        o1 = self._build_valid_o1()

        with self.assertRaises(ValueError) as ctx:
            convert_jvlink_records_to_prerace_feed([ra, se1, se2, o1])
        self.assertIn("Duplicate horse number 1", str(ctx.exception))

    # -------------------------------------------------------------
    # 4. 別レースのオッズ・出走馬の混入防止 (Race Key Mismatch)
    # -------------------------------------------------------------
    def test_mismatched_race_key_rejected(self):
        ra = self._build_valid_ra(race_key="2026102605010101")
        # 異なるレース（中山第2レース: 06010102）の馬やオッズ
        se_other_race = self._build_valid_se(race_key="2026102606010102")
        o1_other_race = self._build_valid_o1(race_key="2026102606010102")

        with self.assertRaises(ValueError) as ctx:
            convert_jvlink_records_to_prerace_feed([ra, se_other_race])
        self.assertIn("Race key mismatch in SE record", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            convert_jvlink_records_to_prerace_feed([ra, self._build_valid_se(), o1_other_race])
        self.assertIn("Race key mismatch in O1 record", str(ctx.exception))

    # -------------------------------------------------------------
    # 5. 未検証レコード（O2〜O7, HR等）の利用禁止
    # -------------------------------------------------------------
    def test_unverified_records_not_ingested(self):
        ra = self._build_valid_ra()
        se = self._build_valid_se()
        o1 = self._build_valid_o1()
        unverified_o3 = b"O3" + b" " * 100  # 馬連オッズ

        # 未検証レコードが含まれていても、除外されて安全にパースされる
        feed = convert_jvlink_records_to_prerace_feed([ra, se, o1, unverified_o3])
        self.assertEqual(len(feed["races"]), 1)
        self.assertNotIn("O3", feed)

    # -------------------------------------------------------------
    # 6. 環境変数や設定JSONの単独改ざんによる認証突破の完全遮断
    # -------------------------------------------------------------
    def test_forged_environment_variable_cannot_bypass_provenance(self):
        raw_feed = {
            "published_at": datetime.now(timezone.utc).isoformat(),
            "source": "jra_van",
            "certified_source": True,  # 自己申告
            "races": []
        }
        feed_file = self.feed_dir / "forged_feed.json"
        feed_file.write_text(json.dumps(raw_feed), encoding="utf-8")

        # KEIBA_JVLINK_CERTIFIED=1 をセットし、provider_config を書き換えても、
        # 取得セッション署名（.acquisition_session.json）が存在しないため遮断される
        with patch.dict(os.environ, {"KEIBA_JVLINK_CERTIFIED": "1"}):
            is_cert, reason = verify_feed_provenance(
                feed_path=feed_file,
                feed_payload=raw_feed,
                provider_config_path=self.provider_config,
                feed_dir=self.feed_dir
            )
            self.assertFalse(is_cert)
            self.assertIn("Acquisition process not connected", reason)

    # -------------------------------------------------------------
    # 7. 正規取得処理によるセッション署名の検証と本番分離
    # -------------------------------------------------------------
    def test_trusted_acquisition_session_signature_and_production_isolation(self):
        raw_feed = {
            "published_at": datetime.now(timezone.utc).isoformat(),
            "source": "jvlink",
            "races": [{"race_id": "20260501"}]
        }
        feed_file = self.feed_dir / "valid_feed.json"
        feed_file.write_text(json.dumps(raw_feed), encoding="utf-8")

        # テストモジュールによるセッション署名を発行
        create_acquisition_signature(
            feed_payload=raw_feed,
            feed_path=feed_file,
            module_name="tests.test_safety_and_verification_suite",
            provider="jra_van",
            output_path=self.feed_dir / ".acquisition_session.json"
        )

        # 本番実行時（test_mode=False かつ KEIBA_TEST_MODE未設定）では、
        # テストセッションによる署名での本番通知を拒絶する
        with patch.dict(os.environ, {"KEIBA_TEST_MODE": "0"}):
            is_cert, reason = verify_feed_provenance(
                feed_path=feed_file,
                feed_payload=raw_feed,
                provider_config_path=self.provider_config,
                feed_dir=self.feed_dir,
                test_mode=False
            )
            self.assertFalse(is_cert)
            self.assertIn("Test acquisition session cannot be used for production alerts", reason)

        # テストモード（test_mode=True）では正常に認定される
        is_cert, reason = verify_feed_provenance(
            feed_path=feed_file,
            feed_payload=raw_feed,
            provider_config_path=self.provider_config,
            feed_dir=self.feed_dir,
            test_mode=True
        )
        self.assertTrue(is_cert)
        self.assertEqual(reason, "PROVENANCE_VERIFIED")

    # -------------------------------------------------------------
    # 8. SDK未導入時の安全停止 (Fail-Closed)
    # -------------------------------------------------------------
    def test_sdk_not_installed_raises_safe_error(self):
        client = JVLinkClient(sid="TEST_SID")
        # SDK未導入環境では connect() で JVLinkNotInstalledError または JVLinkError
        with patch("src.jvlink_client.check_environment_compatibility", return_value={
            "is_windows": True,
            "win32com_available": False,
            "sdk_registered": False
        }):
            with self.assertRaises(JVLinkNotInstalledError) as ctx:
                client.connect()
            self.assertIn("win32com.client is missing", str(ctx.exception))

    # -------------------------------------------------------------
    # 9. 接続診断ツール (scripts/check_jvlink_readiness.py) の診断判定
    # -------------------------------------------------------------
    def test_readiness_checker_output(self):
        report = run_full_readiness_check()
        self.assertIn("overall_status", report)
        self.assertIn(report["overall_status"], ("READY", "SETUP_REQUIRED", "BLOCKED"))
        self.assertTrue(report["pipeline_and_model"]["model_sha256_verified"])
        self.assertEqual(report["pipeline_and_model"]["pipeline_schema_features"], 28)

    # -------------------------------------------------------------
    # 10. JV-Link生レコードから28特徴量・推論へのE2Eパイプライン結合
    # -------------------------------------------------------------
    def test_e2e_jvlink_records_to_pipeline_feed(self):
        ra = self._build_valid_ra()
        se = self._build_valid_se()
        o1 = self._build_valid_o1()

        feed = convert_jvlink_records_to_prerace_feed([ra, se, o1])
        self.assertEqual(feed["source"], "jvlink")
        self.assertEqual(len(feed["races"]), 1)
        race = feed["races"][0]
        self.assertEqual(race["race_id"], "20260501")
        self.assertEqual(race["horses"][0]["win_odds"], 5.2)


if __name__ == "__main__":
    unittest.main()
