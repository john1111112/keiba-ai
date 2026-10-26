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
import base64
import binascii
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



def decode_worker_records(payload: Dict[str, Any], kind: str) -> List[bytes]:
    """Prefer byte-exact base64 JV-Data records over decoded CP932 text.

    The current worker emits both forms. The legacy textual form is accepted
    for old test fixtures, but real acquisitions use the exact byte form.
    """
    lengths = {"ra": (b"RA", 1272), "se": (b"SE", 555), "o1": (b"O1", 962)}
    if kind not in lengths:
        raise ValueError(f"Unsupported JV-Link record kind: {kind}")
    rec_type, rec_length = lengths[kind]
    b64_field = kind + "_records_b64"
    text_field = kind + "_records"
    encoded_items = payload.get(b64_field)
    if encoded_items is not None:
        if not isinstance(encoded_items, list):
            raise JVLinkBridgeError(f"Worker {b64_field} must be a list")
        try:
            records = [base64.b64decode(value, validate=True) for value in encoded_items]
        except (ValueError, TypeError, binascii.Error) as exc:
            raise JVLinkBridgeError(f"Corrupted worker base64 records: {exc}") from exc
        if isinstance(payload.get(text_field), list) and len(records) != len(payload[text_field]):
            raise JVLinkBridgeError(f"Worker record count mismatch: {kind}")
    else:
        # Backward-compatibility for legacy mocked worker payloads.
        items = payload.get(text_field, [])
        if not isinstance(items, list):
            raise JVLinkBridgeError(f"Worker {text_field} must be a list")
        records = [value.encode("cp932", errors="strict") for value in items]
    for record in records:
        if (len(record) != rec_length or record[:2] != rec_type
                or record[-2:] != b"\r\n"):
            raise JVLinkBridgeError(f"Malformed {kind} JV-Link raw record")
    return records


def _verified_32bit_python(candidate: str) -> bool:
    """Execute candidate interpreter to confirm its pointer size, not just its filename."""
    try:
        result = subprocess.run(
            [candidate, "-c", "import struct; print(struct.calcsize('P') * 8)"],
            capture_output=True, text=True, timeout=5, check=False
        )
        return result.returncode == 0 and result.stdout.strip() == "32"
    except (OSError, subprocess.SubprocessError):
        return False


def find_32bit_python() -> Optional[str]:
    """
    32bit Python 実行可能ファイルのパスを探索する。
    1. 環境変数 KEIBA_PYTHON32_PATH
    2. リポジトリ内 vendor/python32/python.exe (スタンドアロン独立環境)
    3. Windows py ランチャー (py -0p) での 32bit インストール
    """
    env_path = os.getenv("KEIBA_PYTHON32_PATH")
    if env_path and Path(env_path).is_file() and _verified_32bit_python(env_path):
        return env_path

    vendor_path = REPO_ROOT / "vendor" / "python32" / "python.exe"
    if vendor_path.is_file() and _verified_32bit_python(str(vendor_path)):
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
                    if parts and Path(parts[-1]).is_file() and _verified_32bit_python(parts[-1]):
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

    # Read the registered ProgID using the 32-bit registry view too:
    # the Streamlit dashboard commonly runs with 64-bit Python.
    sdk_registered = False
    sdk_clsid = None
    if is_windows:
        try:
            import winreg
            for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_CLASSES_ROOT, r"JVDTLab.JVLink\CLSID",
                        0, winreg.KEY_READ | view
                    ) as entry:
                        sdk_clsid = winreg.QueryValueEx(entry, "")[0]
                        sdk_registered = bool(sdk_clsid)
                        if sdk_registered:
                            break
                except OSError:
                    continue
        except (ImportError, OSError):
            sdk_registered = False

    python32_path = find_32bit_python()
    provider_cfg = load_provider_config()
    jra_van_authorized = bool(provider_cfg.get("jra_van_authorized"))

    overall_status = "READY"
    issues = []

    if not is_windows:
        overall_status = "BLOCKED"
        issues.append("Operating system is not Windows (JV-Link requires Windows).")
    elif not python32_path:
        overall_status = "SETUP_REQUIRED"
        issues.append("The JV-Link worker requires 32-bit Python and pywin32.")
    elif not sdk_registered:
        overall_status = "SETUP_REQUIRED"
        issues.append("JV-Link SDK (JVDTLab.JVLink) is not registered in Windows registry.")

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
        self.sid = os.getenv("KEIBA_JVLINK_SID", "").strip() if sid == "UNKNOWN" else sid
        if not self.sid:
            self.sid = "UNKNOWN"
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
                self._link = win32com.client.Dispatch("JVDTLab.JVLink")
            except Exception as exc:
                raise JVLinkError(f"Failed to instantiate JVDTLab.JVLink: {exc}") from exc

        # JVInit 呼び出しと戻り値検証
        try:
            rc = self._link.JVInit(self.sid)
            code = int(rc[0]) if isinstance(rc, (tuple, list)) else int(rc)
            if code != 0:
                self._initialized = False
                self._driver_status = f"INIT_FAILED_CODE_{code}"
                raise JVLinkError(f"JVInit failed with return code {code}")
            self._initialized = True
            # JVStatus counts pending downloads, and must not be used as auth.
        except Exception as exc:
            self._initialized = False
            self._driver_status = "INIT_EXCEPTION"
            raise JVLinkError(f"JVInit execution failed: {exc}") from exc

    def check_service_status(self) -> Dict[str, Any]:
        """Report only evidenced access, not JVStatus download progress.

        The official JV-Link interface defines JVStatus as the number of
        files awaiting download. It is not a subscription/authentication check.
        """
        if not self._initialized:
            status = "NOT_INITIALIZED"
        elif self._driver_status == "AUTHENTICATED":
            status = "AUTHENTICATED"
        else:
            status = "NOT_VERIFIED"
        return {
            "status_code": None,
            "driver_status": status,
            "connected": status == "AUTHENTICATED",
            "message": "Access is only confirmed following successful JV-Link acquisition.",
            "sid": self.sid,
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
        from_time: Optional[str] = None,
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

        if from_time is None:
            from datetime import timedelta
            from_time = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d000000")

        worker_res = self.fetch_via_32bit_worker(
            data_spec=data_spec,
            from_time=from_time,
            output_path=worker_out,
            mode=mode,
            key=key
        )

        if worker_res.get("success") and (worker_res.get("auth_status") != "AUTHENTICATED" or worker_res.get("fetch_status") != "SUCCESS" or not isinstance(worker_res.get("records_count"), int) or worker_res["records_count"] <= 0):
            raise JVLinkError("Inconsistent worker success metadata")

        if not worker_res.get("success"):
            status = worker_res.get("driver_status", "FETCH_FAILED")
            err = worker_res.get("error", "Unknown worker error")
            self._driver_status = status
            raise JVLinkError(f"JV-Link acquisition failed ({status}): {err}")

        # レコードの抽出とバイト変換
        ra_list = decode_worker_records(worker_res, "ra")
        se_list = decode_worker_records(worker_res, "se")
        o1_list = decode_worker_records(worker_res, "o1")
        all_records = ra_list + se_list + o1_list

        if not all_records:
            self._driver_status = "NO_RECORDS_FETCHED"
            raise JVLinkError("Worker reported success but returned 0 valid records (Fail-Closed)")

        # JRA-VAN publishes 0B15 RA/SE separately from 0B31 O1 odds.
        # 0B31 is race-scoped; query using each fully-qualified 16-digit key.
        # An unavailable quote remains missing (never invented).
        odds_records = []
        odds_errors = {}
        if data_spec == "0B15" and mode == "rt":
            race_keys = set()
            for raw in ra_list:
                if len(raw) != 1272:
                    raise JVLinkError("RA record length violates official JV-Data layout")
                race_key = raw[11:27].decode("ascii")
                if len(race_key) != 16 or not race_key.isascii() or not race_key.isdigit():
                    raise JVLinkError("JV-Link returned an invalid race key")
                race_keys.add(race_key)
            for race_key in sorted(race_keys):
                try:
                    odds_result = self.fetch_via_32bit_worker(
                        data_spec="0B31", from_time=from_time,
                        output_path=worker_out, mode="rt",
                        key=race_key
                    )
                    if (not odds_result.get("success")
                            or odds_result.get("auth_status") != "AUTHENTICATED"
                            or odds_result.get("fetch_status") != "SUCCESS"):
                        raise JVLinkError(str(odds_result.get("error") or "No O1 odds"))
                    for raw_odds in decode_worker_records(odds_result, "o1"):
                        if (len(raw_odds) != 962 or raw_odds[:2] != b"O1"
                                or raw_odds[11:27].decode("ascii") != race_key):
                            raise JVLinkError("O1 record invalid or attached to wrong race")
                        odds_records.append(raw_odds)
                except (JVLinkError, OSError, ValueError, UnicodeError) as exc:
                    odds_errors[race_key] = str(exc)
            all_records.extend(odds_records)

        # Raw records are saved before parsing; no silent data-loss exception.
        from src.jvlink_raw_store import store_records
        raw_db = REPO_ROOT / "data" / "jvlink_raw.db"
        try:
            store_records(raw_db, ra_list + se_list + o1_list, data_spec=data_spec)
            if odds_records:
                store_records(raw_db, odds_records, data_spec="0B31")
        except Exception as exc:
            raise JVLinkError(f"Raw JV-Link record persistence failed: {exc}") from exc

        if odds_errors:
            diagnostic = target_dir / "latest_odds_fetch_errors.json"
            diagnostic.write_text(
                json.dumps(odds_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )

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

        feed = convert_jvlink_records_to_prerace_feed(raw_records)
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
