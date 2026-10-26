"""
JV-Link 統合クライアント & 実接続オーケストレーター (JV-Link Integration Client)
- JV-Link SDKのインストール状態・Windows/COM環境の互換性検証
- SDK初期化 (JVInit) 戻り値の厳密検証
- 認証状態確認 (JVStatus) 仕様準拠判定（推測による成功扱いを完全禁止）
- 32bit COM / 64bit Python 互換性ブリッジ (32bitワーカー連携)
- 32bitワーカーによる実データ取得から64bit側でのレコード完全性検証・28特徴量パイプライン結合
- 外部引数による認証偽装の完全廃止（実際の取得結果に基づく証跡発行）
※ 契約や無料体験は一切勝手に開始しない（費用0円）。
"""
from __future__ import annotations
import json
import math
import os
import platform
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

from src.jvlink_adapter import (
    convert_jvlink_records_to_prerace_feed,
    convert_jvlink_batch_to_prerace_feed,
    parse_jv_ra_record,
    parse_jv_se_record,
    parse_jv_o1_record
)
from src.provenance_verifier import create_acquisition_signature, load_provider_config


class JVLinkError(RuntimeError):
    """JV-Link 一般エラー"""
    pass


class JVLinkNotInstalledError(JVLinkError):
    """JV-Link SDK がシステムに未インストール、またはCOM未登録"""
    pass


class JVLinkAuthenticationError(JVLinkError):
    """JV-Link 認証失敗、未契約、または期限切れ"""
    pass


class JVLinkBridgeError(JVLinkError):
    """32bit ワーカープロセスとの通信失敗"""
    pass


# JRA-VAN JVStatus 仕様コード定義
JVSTATUS_MAP = {
    0: ("SERVICE_INACTIVE", False, "サービス休止または未初期化"),
    1: ("AUTHENTICATED", True, "認証成功（正規利用可能）"),
    2: ("MAINTENANCE", False, "JRA-VAN サーバーメンテナンス中"),
    3: ("EXPIRED_OR_UNCONTRACTED", False, "利用権限なし（未契約または無料体験期限切れ）")
}


def find_32bit_python() -> Optional[str]:
    """
    32bit Python 実行可能ファイルのパスを探索する。
    1. 環境変数 KEIBA_PYTHON32_PATH
    2. リポジトリ内 vendor/python32/python.exe (スタンドアロン独立環境)
    3. Windows py ランチャー (py -0p) での 32bit インストール
    """
    env_path = os.getenv("KEIBA_PYTHON32_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    vendor_path = REPO_ROOT / "vendor" / "python32" / "python.exe"
    if vendor_path.is_file():
        return str(vendor_path)

    if platform.system() == "Windows":
        try:
            res = subprocess.run(
                ["py", "-0p"],
                capture_output=True,
                text=True,
                timeout=3
            )
            for line in res.stdout.splitlines():
                if "-32" in line:
                    parts = line.strip().split()
                    if parts and Path(parts[-1]).is_file():
                        return parts[-1]
        except Exception:
            pass

    return None


def check_environment_compatibility() -> Dict[str, Any]:
    """
    Windows環境、32bit/64bitアーキテクチャ、pywin32、SDK登録状態を独立診断する。
    """
    is_windows = (platform.system() == "Windows")
    python_arch = "64bit" if struct.calcsize("P") * 8 == 64 else "32bit"
    python_ver = sys.version.split()[0]
    windows_ver = platform.platform()

    win32com_available = False
    try:
        import win32com.client
        win32com_available = True
    except ImportError:
        pass

    sdk_registered = False
    sdk_clsid = None
    if is_windows:
        try:
            import winreg
            for root in [winreg.HKEY_CLASSES_ROOT]:
                for subkey in ["jvdg.JVLink\\CLSID", "WOW6432Node\\CLSID\\jvdg.JVLink"]:
                    try:
                        with winreg.OpenKey(root, subkey) as key:
                            sdk_clsid = winreg.QueryValue(key, "")
                            sdk_registered = True
                            break
                    except (OSError, WindowsError):
                        continue
                if sdk_registered:
                    break
        except Exception:
            sdk_registered = False

    python32_path = find_32bit_python()
    provider_cfg = load_provider_config()
    jra_van_authorized = bool(provider_cfg.get("jra_van_authorized"))

    overall_status = "READY"
    issues = []

    if not is_windows:
        overall_status = "BLOCKED"
        issues.append("Operating system is not Windows (JV-Link requires Windows).")
    elif not win32com_available:
        overall_status = "SETUP_REQUIRED"
        issues.append("pywin32 (win32com) is not installed in current Python environment.")
    elif not sdk_registered:
        overall_status = "SETUP_REQUIRED"
        issues.append("JV-Link SDK (jvdg.JVLink) is not registered in Windows registry.")

    if not jra_van_authorized:
        overall_status = "SETUP_REQUIRED"
        issues.append("JRA-VAN subscription/trial key is not configured in provider_config.json.")

    return {
        "is_windows": is_windows,
        "windows_version": windows_ver,
        "python_version": python_ver,
        "python_architecture": python_arch,
        "win32com_available": win32com_available,
        "sdk_registered": sdk_registered,
        "sdk_clsid": sdk_clsid,
        "python32_available": bool(python32_path),
        "python32_path": python32_path,
        "jra_van_authorized": jra_van_authorized,
        "overall_status": overall_status,
        "issues": issues
    }


class JVLinkClient:
    """
    JV-Link SDK 操作および特徴量・推論パイプラインへの接続クライアント
    """
    def __init__(self, sid: str = "UNKNOWN", mock_com_obj=None, test_mode: bool = False):
        self.sid = sid
        self._link = mock_com_obj
        self._initialized = False
        self._driver_status = "UNVERIFIED"
        self._test_mode = test_mode or (os.getenv("KEIBA_TEST_MODE") == "1")

    def connect(self) -> None:
        """
        COMオブジェクトのインスタンス化と初期化 (JVInit) を行う。
        戻り値を厳密に検証し、推測による成功扱いは一切行わない。
        """
        if self._link is None:
            compat = check_environment_compatibility()
            if not compat["is_windows"]:
                raise JVLinkError("JV-Link is only supported on Windows")
            if not compat["win32com_available"]:
                raise JVLinkNotInstalledError("win32com.client is missing; install pywin32")
            if not compat["sdk_registered"]:
                raise JVLinkNotInstalledError(
                    "JV-Link SDK is not registered. Download and install JV-Link SDK from JRA-VAN."
                )

            try:
                import win32com.client
                self._link = win32com.client.Dispatch("jvdg.JVLink")
            except Exception as exc:
                raise JVLinkError(f"Failed to instantiate jvdg.JVLink: {exc}") from exc

        # JVInit 呼び出しと戻り値検証
        try:
            rc = self._link.JVInit(self.sid)
            code = int(rc[0]) if isinstance(rc, (tuple, list)) else int(rc)
            if code != 0:
                self._initialized = False
                self._driver_status = f"INIT_FAILED_CODE_{code}"
                raise JVLinkError(f"JVInit failed with return code {code}")
            self._initialized = True
            # 初期化直後はステータス確認を実施
            self.check_service_status()
        except Exception as exc:
            self._initialized = False
            self._driver_status = "INIT_EXCEPTION"
            raise JVLinkError(f"JVInit execution failed: {exc}") from exc

    def check_service_status(self) -> Dict[str, Any]:
        """
        JRA-VANのサービス状態・利用権限ステータスを仕様に基づいて判定。
        戻り値コードを厳密に解析し、未認証・異常値は connected: False とする。
        """
        if not self._initialized:
            self._driver_status = "NOT_INITIALIZED"
            return {
                "status_code": None,
                "driver_status": "NOT_INITIALIZED",
                "connected": False,
                "message": "JVLinkClient is not initialized"
            }

        try:
            status_raw = self._link.JVStatus()
            code = int(status_raw[0]) if isinstance(status_raw, (tuple, list)) else int(status_raw)
        except Exception as exc:
            self._driver_status = "STATUS_CALL_FAILED"
            return {
                "status_code": -999,
                "driver_status": "STATUS_CALL_FAILED",
                "connected": False,
                "message": f"JVStatus call failed: {exc}"
            }

        # 仕様マッピング判定
        if code in JVSTATUS_MAP:
            driver_status, connected, desc = JVSTATUS_MAP[code]
        else:
            driver_status = f"UNVERIFIED_STATUS_{code}"
            connected = False
            desc = f"Unverified status code: {code}"

        self._driver_status = driver_status

        return {
            "status_code": code,
            "driver_status": driver_status,
            "connected": connected,
            "message": desc,
            "sid": self.sid
        }

    def fetch_via_32bit_worker(
        self,
        data_spec: str,
        from_time: str,
        output_path: Optional[Path] = None,
        mode: str = "rt",
        key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        64bit Python から 32bit Python サブプロセスを介してデータを安全に取得するブリッジ。
        【安全性強化】
        - 呼び出しごとに一意の一時ファイルパスを使用し、古い出力の誤読を防止
        - subprocess.run() の終了コードとタイムアウトを厳格検証
        - 出力JSONの内容（sid, data_spec, タイムスタンプ）を厳密照合
        - 完了後に一時ファイルを確実に削除
        """
        import uuid
        import tempfile

        python32 = find_32bit_python()
        if not python32:
            raise JVLinkBridgeError("No 32-bit Python executable found to run JV-Link worker")

        unique_id = uuid.uuid4().hex
        if output_path is None:
            tmp_dir = Path(tempfile.gettempdir()) / "keiba_jvlink_bridge"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            target_out = tmp_dir / f"worker_{unique_id}.json"
        else:
            target_out = Path(str(output_path) + f".{unique_id}.tmp")

        # 既存ファイルの残骸があれば削除
        if target_out.is_file():
            target_out.unlink()

        worker_script = REPO_ROOT / "src" / "jvlink_worker.py"
        cmd = [
            python32,
            str(worker_script),
            "--sid", self.sid,
            "--spec", data_spec,
            "--from-time", from_time,
            "--mode", mode,
            "--out", str(target_out)
        ]
        if key:
            cmd.extend(["--key", key])

        invocation_start_ts = int(time.time()) - 2

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode != 0:
                err_detail = res.stderr.strip() or res.stdout.strip()
                raise JVLinkBridgeError(f"Worker exited with non-zero code {res.returncode}: {err_detail}")

            if not target_out.is_file():
                raise JVLinkBridgeError(f"Worker finished but output file was not created: {res.stderr}")

            try:
                payload = json.loads(target_out.read_text(encoding="utf-8"))
            except Exception as exc:
                raise JVLinkBridgeError(f"Failed to parse worker output JSON: {exc}")

            if not isinstance(payload, dict):
                raise JVLinkBridgeError("Worker output JSON is not a dictionary")

            # 内容の整合性照合（古い出力の誤読防止）
            out_ts = payload.get("timestamp", 0)
            if out_ts < invocation_start_ts:
                raise JVLinkBridgeError(f"Stale worker output detected (timestamp {out_ts} < invocation {invocation_start_ts})")
            if payload.get("sid") != self.sid or payload.get("data_spec") != data_spec:
                raise JVLinkBridgeError("Worker output metadata mismatch (sid/data_spec)")

            return payload

        except subprocess.TimeoutExpired as exc:
            raise JVLinkBridgeError(f"32-bit worker timed out after {exc.timeout} seconds") from exc
        finally:
            if target_out.is_file():
                try:
                    target_out.unlink()
                except Exception:
                    pass

    def fetch_and_build_live_feed(
        self,
        data_spec: str = "0B15",
        from_time: str = "20261026000000",
        mode: str = "rt",
        key: Optional[str] = None,
        output_dir: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        32bit ワーカーを介して実レースデータを取得し、完全性検証を経て
        発走前28特徴量パイプライン用フィードを構築。
        実データ取得成功（records_count > 0 かつ AUTHENTICATED）時のみ認証証跡を発行。
        """
        target_dir = Path(output_dir or (REPO_ROOT / "data" / "authorized_feed"))
        target_dir.mkdir(parents=True, exist_ok=True)
        worker_out = target_dir / "worker_output.json"

        worker_res = self.fetch_via_32bit_worker(
            data_spec=data_spec,
            from_time=from_time,
            output_path=worker_out,
            mode=mode,
            key=key
        )

        if worker_res.get("success") and (
            worker_res.get("auth_status") != "AUTHENTICATED"
            or worker_res.get("fetch_status") != "SUCCESS"
            or not isinstance(worker_res.get("records_count"), int)
            or worker_res["records_count"] <= 0
        ):
            raise JVLinkError("Inconsistent worker success status")

        if worker_res.get("success") and (worker_res.get("auth_status") != "AUTHENTICATED" or worker_res.get("fetch_status") != "SUCCESS" or not isinstance(worker_res.get("records_count"), int) or worker_res["records_count"] <= 0):
            raise JVLinkError("Inconsistent worker success metadata")

        if not worker_res.get("success"):
            status = worker_res.get("driver_status", "FETCH_FAILED")
            err = worker_res.get("error", "Unknown worker error")
            self._driver_status = status
            raise JVLinkError(f"JV-Link acquisition failed ({status}): {err}")

        # レコードの抽出とバイト変換
        ra_list = [r.encode("cp932") for r in worker_res.get("ra_records", [])]
        se_list = [r.encode("cp932") for r in worker_res.get("se_records", [])]
        o1_list = [r.encode("cp932") for r in worker_res.get("o1_records", [])]
        all_records = ra_list + se_list + o1_list

        if not all_records:
            self._driver_status = "NO_RECORDS_FETCHED"
            raise JVLinkError("Worker reported success but returned 0 valid records (Fail-Closed)")

        self._driver_status = "AUTHENTICATED"
        return self.process_records_to_feed(all_records, output_dir=target_dir)

    def process_records_to_feed(
        self,
        raw_records: List[bytes],
        output_dir: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        生レコード群 (RA, SE, O1) を変換し、正規フィード辞書を構築。
        外部引数による driver_status 偽装は禁止し、内部検証結果からのみ証跡発行。
        """
        if not raw_records:
            raise ValueError("raw_records must not be empty")

        feed = convert_jvlink_batch_to_prerace_feed(raw_records)
        target_dir = Path(output_dir or (REPO_ROOT / "data" / "authorized_feed"))
        target_dir.mkdir(parents=True, exist_ok=True)
        feed_file = target_dir / "latest_jvlink_feed.json"

        feed_file.write_text(json.dumps(feed, ensure_ascii=False, indent=2), encoding="utf-8")

        # 内部検証ステータス（self._driver_status）のみを使用
        verified_driver_status = self._driver_status

        create_acquisition_signature(
            feed_payload=feed,
            feed_path=feed_file,
            module_name="src.jvlink_client",
            provider="jra_van",
            output_path=target_dir / ".acquisition_session.json",
            test_mode=self._test_mode,
            driver_status=verified_driver_status
        )

        return feed
