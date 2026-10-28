"""
第6回JV-Link実接続環境・認証処理・レースID衝突防止 総合検証テストスイート
(JV-Link Real Connection, Authenticity & Race ID Disambiguation Test Suite)

【検証項目】
1. 固定秘密鍵が存在しない（ハードコード秘密鍵の完全撤去）
2. 秘密鍵未設定時は本番署名を発行できない (Fail-Closed)
3. 偽造署名を拒絶する
4. 期限切れ署名を拒絶する
5. JV-Link未認証時は本番通知を禁止する (driver_status != 'AUTHENTICATED')
6. JVStatus の異常値（負数や未知コード）を成功扱いしない
7. 異なる開催日のレースIDが衝突しない（16桁完全レースキーの検証）
8. RA・SE・O1のレースキー不一致を拒絶する
9. 32bit/64bit環境の不一致を検出する
10. SDK未導入時に安全停止する
11. 32bit取得プロセスと64bit推論プロセス間のデータ受け渡し検証
12. 実データ未接続時に誤通知しない
"""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
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
    normalize_to_16char_race_key,
    UNVERIFIED_RECORD_TYPES
)
from src.jvlink_client import (
    check_environment_compatibility,
    find_32bit_python,
    JVLinkClient,
    JVLinkNotInstalledError,
    JVLinkError,
    JVLinkBridgeError,
    JVSTATUS_MAP
)
from src.provenance_verifier import (
    verify_feed_provenance,
    create_acquisition_signature,
    load_provider_config,
    get_provenance_secret
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

    def _rec(self, kind: str, size: int, race: str, kubun: str = "2", created: str = "20261026") -> bytearray:
        record = bytearray(b" " * (size - 2) + b"\r\n")
        record[:2] = kind.encode("ascii")
        record[2:3] = kubun.encode("ascii")
        record[3:11] = created.encode("ascii")
        record[11:27] = race.encode("ascii")
        return record

    def _build_valid_ra(self, race_key=None, distance="1600", track="11", cond="00", post_time="1500") -> bytes:
        rkey = race_key or self.race_key
        raw = self._rec("RA", 1272, rkey)
        raw[697:701] = str(distance).encode("ascii")
        raw[705:707] = str(track).encode("ascii")
        raw[873:877] = str(post_time).encode("ascii")
        raw[881:883] = b"01"
        raw[888:890] = str(cond).encode("ascii")
        return bytes(raw)

    def _build_valid_se(self, race_key=None, horse_num="01", b_num="1", horse_id="2022100001",
                        sex="1", age="04", weight_carry="560", jockey_id="01234") -> bytes:
        rkey = race_key or self.race_key
        raw = self._rec("SE", 555, rkey)
        raw[27:28] = str(b_num).encode("ascii")
        raw[28:30] = str(horse_num).zfill(2).encode("ascii")
        raw[30:40] = str(horse_id).encode("ascii")
        raw[40:76] = "テストホース".encode("cp932").ljust(36, b" ")
        raw[78:79] = str(sex).encode("ascii")
        raw[82:84] = str(age).zfill(2).encode("ascii")
        raw[288:291] = str(weight_carry).encode("ascii")
        raw[296:301] = str(jockey_id).encode("ascii")
        raw[324:327] = b"480"
        raw[328:331] = b"+02"
        return bytes(raw)

    def _build_valid_o1(self, race_key=None, odds_str="0052", obs_time="10261200") -> bytes:
        rkey = race_key or self.race_key
        raw = self._rec("O1", 962, rkey, kubun="1")
        raw[27:35] = str(obs_time).encode("ascii")
        raw[35:37] = b"01"
        raw[37:39] = b"01"
        raw[39:40] = b"7"
        raw[40:41] = b"7"
        raw[42:43] = b"3"
        raw[43:51] = b"01" + str(odds_str).encode("ascii") + b"01"
        return bytes(raw)

    # -------------------------------------------------------------
    # 1. 固定秘密鍵が存在しないことの検証
    # -------------------------------------------------------------
    def test_no_hardcoded_secret_key_in_provenance_verifier(self):
        import inspect
        import src.provenance_verifier as pv
        source = inspect.getsource(pv)
        self.assertNotIn("keiba_ai_trusted_session_key_v5", source)
        self.assertNotIn("default_secret", source.lower())

    # -------------------------------------------------------------
    # 2. 秘密鍵未設定時は本番署名を発行・検証できない (Fail-Closed)
    # -------------------------------------------------------------
    def test_missing_secret_key_fails_closed(self):
        # 環境変数 KEIBA_PROVENANCE_SECRET 未設定かつキーファイル未存在で自動作成禁止時
        with patch.dict(os.environ, {"KEIBA_TEST_MODE": "0"}, clear=True):
            if "KEIBA_PROVENANCE_SECRET" in os.environ:
                del os.environ["KEIBA_PROVENANCE_SECRET"]
            with patch("src.provenance_verifier.DEFAULT_KEY_FILE", self.dir_path / "non_existent_key.dat"):
                with self.assertRaises(RuntimeError) as ctx:
                    get_provenance_secret(test_mode=False, allow_create_prod_key=False)
                self.assertIn("No secure provenance secret configured", str(ctx.exception))

    # -------------------------------------------------------------
    # 3. 偽造署名を拒絶する
    # -------------------------------------------------------------
    def test_forged_session_signature_rejected(self):
        raw_feed = {"published_at": datetime.now(timezone.utc).isoformat(), "source": "jvlink", "races": []}
        feed_file = self.feed_dir / "forged_feed.json"
        feed_file.write_text(json.dumps(raw_feed), encoding="utf-8")

        # 不正な偽造署名
        session_file = self.feed_dir / ".acquisition_session.json"
        session_file.write_text(json.dumps({
            "feed_sha256": "fake_hash_12345",
            "feed_path": str(feed_file),
            "module_name": "src.jvlink_client",
            "provider": "jra_van",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "timestamp": int(time.time()),
            "nonce": "test_nonce",
            "driver_status": "AUTHENTICATED",
            "signature": "forged_hmac_hex"
        }), encoding="utf-8")

        is_cert, reason = verify_feed_provenance(feed_file, raw_feed, self.provider_config, self.feed_dir, test_mode=True)
        self.assertFalse(is_cert)
        self.assertIn("signature mismatch", reason)

    # -------------------------------------------------------------
    # 4. 期限切れ署名を拒絶する
    # -------------------------------------------------------------
    def test_expired_session_signature_rejected(self):
        raw_feed = {"published_at": datetime.now(timezone.utc).isoformat(), "source": "jvlink", "races": []}
        feed_file = self.feed_dir / "expired_feed.json"
        feed_file.write_text(json.dumps(raw_feed), encoding="utf-8")

        # 2時間前のタイムスタンプで署名作成
        old_ts = int(time.time()) - 7200
        with patch("time.time", return_value=old_ts):
            create_acquisition_signature(
                feed_payload=raw_feed,
                feed_path=feed_file,
                module_name="tests.test_jvlink_real_connection_readiness",
                provider="jra_van",
                output_path=self.feed_dir / ".acquisition_session.json",
                test_mode=True,
                driver_status="AUTHENTICATED"
            )

        is_cert, reason = verify_feed_provenance(feed_file, raw_feed, self.provider_config, self.feed_dir, test_mode=True)
        self.assertFalse(is_cert)
        self.assertIn("expired", reason.lower())

    # -------------------------------------------------------------
    # 5. JV-Link未認証時は本番通知を禁止する (Fail-Closed)
    # -------------------------------------------------------------
    def test_unauthenticated_driver_status_blocks_alert(self):
        raw_feed = {"published_at": datetime.now(timezone.utc).isoformat(), "source": "jvlink", "races": []}
        feed_file = self.feed_dir / "unauth_feed.json"
        feed_file.write_text(json.dumps(raw_feed), encoding="utf-8")

        # driver_status が "SERVICE_INACTIVE" や "EXPIRED_OR_UNCONTRACTED"
        create_acquisition_signature(
            feed_payload=raw_feed,
            feed_path=feed_file,
            module_name="tests.test_jvlink_real_connection_readiness",
            provider="jra_van",
            output_path=self.feed_dir / ".acquisition_session.json",
            test_mode=True,
            driver_status="EXPIRED_OR_UNCONTRACTED"
        )

        is_cert, reason = verify_feed_provenance(feed_file, raw_feed, self.provider_config, self.feed_dir, test_mode=True)
        self.assertFalse(is_cert)
        self.assertIn("driver session is not authenticated", reason)

    # -------------------------------------------------------------
    # 6. JVStatus の異常値を成功扱いしない
    # -------------------------------------------------------------
    def test_jvstatus_abnormal_values_not_treated_as_connected(self):
        mock_com = MagicMock()
        mock_com.JVInit.return_value = 0

        client = JVLinkClient(sid="TEST", mock_com_obj=mock_com, test_mode=True)
        client.connect()

        # コード 0: 取得前は NOT_VERIFIED, connected=False
        mock_com.JVStatus.return_value = 0
        s0 = client.check_service_status()
        self.assertFalse(s0["connected"])
        self.assertEqual(s0["driver_status"], "NOT_VERIFIED")

        # コード 3: 未取得時は NOT_VERIFIED, connected=False
        mock_com.JVStatus.return_value = 3
        s3 = client.check_service_status()
        self.assertFalse(s3["connected"])
        self.assertEqual(s3["driver_status"], "NOT_VERIFIED")

        # 未知のコード -99 -> UNVERIFIED & False
        mock_com.JVStatus.return_value = -99
        s_unknown = client.check_service_status()
        self.assertFalse(s_unknown["connected"])
        self.assertEqual(s_unknown["driver_status"], "NOT_VERIFIED")

        # JVStatus は進捗カウンタのため、戻り値が 1 でも取得成功前は connected: False
        mock_com.JVStatus.return_value = 1
        s1 = client.check_service_status()
        self.assertFalse(s1["connected"])
        self.assertEqual(s1["driver_status"], "NOT_VERIFIED")

        # 実データ取得成功によって driver_status が AUTHENTICATED に遷移した時のみ connected: True
        client._driver_status = "AUTHENTICATED"
        s_auth = client.check_service_status()
        self.assertTrue(s_auth["connected"])
        self.assertEqual(s_auth["driver_status"], "AUTHENTICATED")

    # -------------------------------------------------------------
    # 7. 異なる開催日のレースIDが衝突しないことの検証
    # -------------------------------------------------------------
    def test_different_dates_do_not_collide_race_id(self):
        # 東京05場, 01回, 01日, 01R: 2026年10月24日
        ra_day1 = self._build_valid_ra(race_key="2026102405010101")
        # 東京05場, 01回, 02日, 01R: 2026年10月25日
        ra_day2 = self._build_valid_ra(race_key="2026102505010201")

        parsed1 = parse_jv_ra_record(ra_day1)
        parsed2 = parse_jv_ra_record(ra_day2)

        # 16桁の完全レースキーが race_id となっているため、衝突しない！
        self.assertNotEqual(parsed1["race_id"], parsed2["race_id"])
        self.assertEqual(parsed1["race_id"], "2026102405010101")
        self.assertEqual(parsed2["race_id"], "2026102505010201")

        # 旧8桁形式の比較（旧形式では 20260501 で衝突していた）
        self.assertEqual(parsed1["legacy_race_id"], parsed2["legacy_race_id"])
        # 架空補完撤去の検証: 旧8桁は 0101 を捏造せず None（未解決）を返す
        self.assertIsNone(normalize_to_16char_race_key(parsed1["legacy_race_id"]))
        # 16桁正規キーはそのまま返却
        self.assertEqual(normalize_to_16char_race_key(parsed1["race_id"]), "2026102405010101")

    # -------------------------------------------------------------
    # 8. RA・SE・O1のレースキー不一致を拒絶する
    # -------------------------------------------------------------
    def test_mismatched_race_key_rejected(self):
        ra = self._build_valid_ra(race_key="2026102605010101")
        se_other = self._build_valid_se(race_key="2026102606010102")  # 中山2R
        with self.assertRaises(ValueError) as ctx:
            convert_jvlink_records_to_prerace_feed([ra, se_other])
        self.assertIn("Race key mismatch", str(ctx.exception))

    # -------------------------------------------------------------
    # 9. 32bit/64bit環境の不一致・診断の独立性
    # -------------------------------------------------------------
    def test_architecture_compatibility_check(self):
        compat = check_environment_compatibility()
        self.assertIn("python_architecture", compat)
        self.assertIn("win32com_available", compat)
        self.assertIn("sdk_registered", compat)
        self.assertIn("python32_available", compat)
        self.assertIsInstance(compat["issues"], list)

    # -------------------------------------------------------------
    # 10. SDK未導入時に安全停止する
    # -------------------------------------------------------------
    def test_sdk_not_installed_fails_safely(self):
        client = JVLinkClient(sid="TEST", mock_com_obj=None, test_mode=True)
        with patch("src.jvlink_client.check_environment_compatibility", return_value={
            "is_windows": True,
            "win32com_available": True,
            "sdk_registered": False
        }):
            with self.assertRaises(JVLinkNotInstalledError) as ctx:
                client.connect()
            self.assertIn("JV-Link SDK is not registered", str(ctx.exception))

    # -------------------------------------------------------------
    # 11. 32bitワーカープロセスとの連携・ブリッジ検証
    # -------------------------------------------------------------
    def test_32bit_worker_bridge_handling(self):
        client = JVLinkClient(sid="TEST_SID", test_mode=True)
        # 互換Pythonワーカーが存在しない場合は JVLinkBridgeError を安全に送出
        with patch("src.jvlink_client.find_jvlink_python", return_value=None):
            with self.assertRaises(JVLinkBridgeError) as ctx:
                client.fetch_via_32bit_worker("0B15", "202610260000", self.dir_path / "out.json")
            self.assertTrue("Compatible 32/64-bit JV-Link SDK/Python worker not found" in str(ctx.exception) or "No 32-bit Python executable found" in str(ctx.exception))

    # -------------------------------------------------------------
    # 12. 実データ未接続時に誤通知しない (E2E)
    # -------------------------------------------------------------
    def test_unconnected_real_data_does_not_fire_alert(self):
        ra = self._build_valid_ra()
        se = self._build_valid_se()
        o1 = self._build_valid_o1()

        feed = convert_jvlink_records_to_prerace_feed([ra, se, o1])
        self.assertEqual(feed["races"][0]["race_id"], "2026102605010101")
        feed_file = self.feed_dir / "latest.json"
        feed_file.write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")

        # セッション署名がない状態では、スキャナを実行しても通知は0件
        res = run_monitor_scan(
            feed_path=feed_file,
            state_db_path=self.state_db,
            config_path=self.config_path,
            provider_config_path=self.provider_config
        )
        self.assertEqual(res["notifications_sent"], 0)
        self.assertTrue(res["safety_lock_enabled"])


    # -------------------------------------------------------------
    # 13. 取得件数0件のワーカーを成功扱いしない (Fail-Closed)
    # -------------------------------------------------------------
    def test_zero_records_worker_fails_closed(self):
        client = JVLinkClient(sid="TEST_SID", test_mode=True)
        # ワーカーが 0 件のレコードを返したシチュエーション
        empty_worker_res = {
            "success": True,  # 形式上 success と返ってきても
            "driver_status": "AUTHENTICATED",
            "records_count": 0,
            "ra_records": [],
            "se_records": [],
            "o1_records": []
        }
        with patch.object(client, "fetch_via_32bit_worker", return_value=empty_worker_res):
            with self.assertRaises(JVLinkError) as ctx:
                client.fetch_and_build_live_feed(output_dir=self.feed_dir)
            self.assertTrue("Inconsistent worker success metadata" in str(ctx.exception) or "0 valid records" in str(ctx.exception))

    # -------------------------------------------------------------
    # 14. 32bitワーカーから64bitパイプラインへの正常データ転送・推論
    # -------------------------------------------------------------
    def test_32bit_worker_to_64bit_pipeline_transfer(self):
        client = JVLinkClient(sid="TEST_SID", test_mode=True)
        ra_text = self._build_valid_ra().decode("cp932")
        se_text = self._build_valid_se().decode("cp932")
        o1_text = self._build_valid_o1().decode("cp932")

        valid_worker_res = {
            "success": True,
            "auth_status": "AUTHENTICATED",
            "fetch_status": "SUCCESS",
            "driver_status": "AUTHENTICATED",
            "records_count": 3,
            "ra_records": [ra_text],
            "se_records": [se_text],
            "o1_records": [o1_text]
        }
        with patch.object(client, "fetch_via_32bit_worker", return_value=valid_worker_res):
            feed = client.fetch_and_build_live_feed(output_dir=self.feed_dir)
            self.assertEqual(len(feed["races"]), 1)
            self.assertEqual(feed["races"][0]["race_id"], "2026102605010101")
            self.assertEqual(feed["races"][0]["horses"][0]["win_odds"], 5.2)

        # 真正性セッション署名も AUTHENTICATED として発行されていること
        is_cert, reason = verify_feed_provenance(
            self.feed_dir / "latest_jvlink_feed.json",
            feed,
            self.provider_config,
            self.feed_dir,
            test_mode=True
        )
        self.assertTrue(is_cert)
        self.assertEqual(reason, "PROVENANCE_VERIFIED")

    # -------------------------------------------------------------
    # 15. 古いワーカー出力の誤読防止・メタデータ照合
    # -------------------------------------------------------------
    def test_stale_worker_output_rejected(self):
        client = JVLinkClient(sid="TEST_SID", test_mode=True)
        client._worker_interpreter_cache = ("fake_python32.exe", 32)
        def fake_worker_run(cmd, *args, **kwargs):
            if "--out" not in cmd:
                res = MagicMock()
                res.returncode = 0
                return res
            out_idx = cmd.index("--out") + 1
            out_file = Path(cmd[out_idx])
            out_file.write_text(json.dumps({
                "success": True,
                "timestamp": int(time.time()) - 100,
                "sid": "OTHER_SID",
                "data_spec": "0B15"
            }), encoding="utf-8")
            res = MagicMock()
            res.returncode = 0
            return res

        with patch("subprocess.run", side_effect=fake_worker_run):
            with self.assertRaises(JVLinkBridgeError) as ctx:
                client.fetch_via_32bit_worker("0B15", "202610260000")
            self.assertTrue("Stale worker output" in str(ctx.exception) or "mismatch" in str(ctx.exception))
    def test_worker_timeout_raises_bridge_error(self):
        client = JVLinkClient(sid="TEST_SID", test_mode=True)
        client._worker_interpreter_cache = ("fake_python32.exe", 32)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=60)):
            with self.assertRaises(JVLinkBridgeError) as ctx:
                client.fetch_via_32bit_worker("0B15", "202610260000")
            self.assertIn("timed out", str(ctx.exception))


    # -------------------------------------------------------------
    # 17. 複数レースの同時取得・完全分離とレース間混入防止
    # -------------------------------------------------------------
    def test_multi_race_batch_parsing_and_isolation(self):
        from src.jvlink_adapter import convert_multiple_jvlink_records_to_feed

        race1_key = "2026102605010101"  # 東京1R
        race2_key = "2026102605010102"  # 東京2R

        # レース1のレコード
        ra1 = self._build_valid_ra(race_key=race1_key, distance="1600")
        se1_h1 = self._build_valid_se(race_key=race1_key, horse_num="01", horse_id="2022100001")
        se1_h2 = self._build_valid_se(race_key=race1_key, horse_num="02", horse_id="2022100002")
        o1_1 = self._build_valid_o1(race_key=race1_key, odds_str="0025")  # 単勝2.5倍

        # レース2のレコード
        ra2 = self._build_valid_ra(race_key=race2_key, distance="2000")
        se2_h1 = self._build_valid_se(race_key=race2_key, horse_num="01", horse_id="2022100003")
        se2_h2 = self._build_valid_se(race_key=race2_key, horse_num="02", horse_id="2022100004")
        o1_2 = self._build_valid_o1(race_key=race2_key, odds_str="0045")  # 単勝4.5倍

        # 意図的に混在させた順序（JRA-VANの実際の配信順序に近いシャッフル）
        mixed_records = [ra2, se1_h1, o1_1, ra1, se2_h1, se1_h2, o1_2, se2_h2]

        feed = convert_multiple_jvlink_records_to_feed(mixed_records)

        # 2レースが抽出されていること
        self.assertEqual(len(feed["races"]), 2)

        # レースキー順にソートされていること
        r1 = feed["races"][0]
        r2 = feed["races"][1]
        self.assertEqual(r1["race_id"], race1_key)
        self.assertEqual(r2["race_id"], race2_key)

        # レース1の出走馬・オッズが独立していること
        self.assertEqual(r1["distance"], 1600.0)
        self.assertEqual(len(r1["horses"]), 2)
        self.assertEqual(r1["horses"][0]["horse_number"], 1)
        self.assertEqual(r1["horses"][0]["horse_id"], "2022100001")
        self.assertEqual(r1["horses"][0]["win_odds"], 2.5)
        self.assertEqual(r1["horses"][1]["horse_number"], 2)
        self.assertEqual(r1["horses"][1]["horse_id"], "2022100002")

        # レース2の出走馬・オッズが独立していること
        self.assertEqual(r2["distance"], 2000.0)
        self.assertEqual(len(r2["horses"]), 2)
        self.assertEqual(r2["horses"][0]["horse_number"], 1)
        self.assertEqual(r2["horses"][0]["horse_id"], "2022100003")
        self.assertEqual(r2["horses"][0]["win_odds"], 4.5)
        self.assertEqual(r2["horses"][1]["horse_number"], 2)
        self.assertEqual(r2["horses"][1]["horse_id"], "2022100004")


if __name__ == "__main__":
    unittest.main()
