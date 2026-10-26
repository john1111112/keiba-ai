"""
JV-Link 32bit 専用取得ワーカー (JV-Link 32-bit Acquisition Worker)
- 32bit COM DLL である JV-Link SDK (JVDTLab.JVLink) と直接通信する専用プロセス
- JRA-VAN公式仕様に基づく API 呼び出し:
  - JVInit(sid): 初期化
  - JVStatus(): サービス利用権限・認証状態確認
  - JVRTOpen(dataspec, key) / JVOpen(dataspec, fromtime, option): オープン処理 (fromtime 14桁正規化)
  - JVRead(buffsize): レコード取得ループ (EOF, 改ページ, ダウンロード待機処理)
  - JVClose(): 確実なクローズ処理 (finally)
- RA, SE, O1 レコードの完全性検証と抽出
- 認証状態（auth_status）と取得状態（fetch_status）を厳密に分離管理
- レコード取得成功判定の厳格化:
  - SDK初期化成功 (0)
  - JVStatus認証成功 (1: AUTHENTICATED)
  - オープンAPI成功 (>= 0)
  - RAレコードが最低1件存在
  - SEレコードが最低1件存在しRAとレースキー完全一致
  - レコード欠損・破損なし
  上記をすべて満たす場合のみ fetch_status='SUCCESS' かつ overall success=True とする。
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent


def check_is_32bit() -> bool:
    """現在のプロセスが32bitアーキテクチャか判定"""
    return struct.calcsize("P") * 8 == 32


def normalize_fromtime(from_time: str) -> str:
    """
    JVOpen の fromtime 引数を JRA-VAN 公式仕様の 14 桁 (YYYYMMDDHHMMSS) に正規化。
    """
    digits = "".join(c for c in from_time if c.isdigit())
    if len(digits) == 14:
        return digits
    if len(digits) == 12:
        return digits + "00"
    if len(digits) == 8:
        return digits + "000000"
    raise ValueError(f"Invalid fromtime format: '{from_time}' (expected 8, 12, or 14 digits)")


def validate_records_integrity(ra_records: List[str], se_records: List[str], o1_records: List[str]) -> Tuple[bool, str]:
    """
    取得された RA・SE・O1 レコードの完全性とレースキー整合性を検証。
    """
    if not ra_records:
        return False, "No RA (race header) record found"
    if not se_records:
        return False, "No SE (runner) record found"

    ra_keys = set()
    for i, ra in enumerate(ra_records):
        key = ra[11:27]
        if len(key) != 16 or not key.isascii() or not key.isdigit():
            return False, f"Invalid RA race key at index {i}"
        ra_keys.add(key)

    se_keys = set()
    for i, se in enumerate(se_records):
        key = se[11:27]
        if len(se.encode("cp932")) != 555 or key not in ra_keys:
            return False, f"Invalid SE record or unmatched race at index {i}: {key}"
        se_keys.add(key)
    if se_keys != ra_keys:
        return False, f"Races without runners: {sorted(ra_keys - se_keys)}"

    for i, o1 in enumerate(o1_records):
        key = o1[11:27]
        if len(o1.encode("cp932")) != 962 or key not in ra_keys:
            return False, f"Invalid O1 record or unmatched race at index {i}: {key}"

    return True, "INTEGRITY_VERIFIED"


def execute_jvlink_fetch(
    link,
    data_spec: str,
    from_time: str,
    mode: str = "rt",
    key: Optional[str] = None,
    max_wait_seconds: float = 30.0,
    buffer_size: int = 110000
) -> Dict[str, Any]:
    """
    JRA-VAN 公式仕様に基づく JVRead ループによるデータ取得処理
    """
    opened = False
    raw_records: List[str] = []
    ra_records: List[str] = []
    se_records: List[str] = []
    o1_records: List[str] = []
    ra_records_b64: List[str] = []
    se_records_b64: List[str] = []
    o1_records_b64: List[str] = []

    error_msg = None

    try:
        if mode == "rt":
            # JVRTOpen(dataspec, key): リアルタイム系オッズ・出馬表
            rt_key = key or from_time[:8]
            open_res = link.JVRTOpen(data_spec, rt_key)
        else:
            # JVOpen(dataspec, fromtime, option): 蓄積系データ (fromtime 14桁)
            normalized_time = normalize_fromtime(from_time)
            open_res = link.JVOpen(data_spec, normalized_time, 1, 0, 0, "")

        rc = int(open_res[0]) if isinstance(open_res, (tuple, list)) else int(open_res)
        if rc < 0:
            return {
                "fetch_status": f"OPEN_FAILED_CODE_{rc}",
                "error": f"JVOpen/JVRTOpen failed with return code {rc}",
                "records_count": 0,
                "ra_count": 0,
                "se_count": 0,
                "o1_count": 0,
                "ra_records": [],
                "se_records": [],
                "o1_records": []
            }

        opened = True
        waiting_since = None
        wait_count = 0
        control_markers = 0

        # 公式仕様に基づく JVRead ループ
        while True:
            try:
                response = link.JVGets(None, buffer_size, "")
            except Exception as exc:
                error_msg = f"JVGets COM invocation failed: {exc}"
                break

            if isinstance(response, (tuple, list)):
                read_rc = int(response[0])
                buff = response[1] if len(response) > 1 else ""
            else:
                read_rc = int(response)
                buff = ""

            # 0: EOF (全データ読込完了)
            if read_rc == 0:
                break

            # -1: ファイル切り替え（改ページマーカー）
            if read_rc == -1:
                control_markers += 1
                if control_markers > 1200:
                    error_msg = "Exceeded maximum file switch markers (-1)"
                    break
                continue

            # -3: ダウンロード待機中
            if read_rc == -3:
                wait_count += 1
                if wait_count > 60:
                    error_msg = "Download wait count exceeded threshold (-3)"
                    break
                if waiting_since is None:
                    waiting_since = time.monotonic()
                if time.monotonic() - waiting_since >= max_wait_seconds:
                    error_msg = "Download wait timeout exceeded"
                    break
                time.sleep(0.5)
                continue

            # 負のエラーコード
            if read_rc < 0:
                error_msg = f"JVRead failed with negative error code: {read_rc}"
                break

            waiting_since = None
            wait_count = 0
            control_markers = 0

            # JVGets preserves Shift-JIS bytes in a COM byte SAFEARRAY.
            if isinstance(buff, (bytes, bytearray, memoryview)):
                raw = bytes(buff)
            elif isinstance(buff, (tuple, list)) and all(
                isinstance(value, int) and 0 <= value < 256 for value in buff
            ):
                raw = bytes(buff)
            else:
                error_msg = f"Invalid JVGets byte buffer: {type(buff).__name__}"
                break
            if len(raw) < read_rc or not raw[:read_rc].endswith(b"\r\n"):
                error_msg = "JVGets returned truncated or malformed record bytes"
                break
            try:
                text = raw[:read_rc].decode("cp932", errors="strict")
            except UnicodeDecodeError as exc:
                error_msg = f"JVGets returned invalid CP932 data: {exc}"
                break

            record_type = text[:2]
            raw_records.append(text)
            if record_type == "RA":
                ra_records.append(text)
                ra_records_b64.append(base64.b64encode(raw[:read_rc]).decode("ascii"))
            elif record_type == "SE":
                se_records.append(text)
                se_records_b64.append(base64.b64encode(raw[:read_rc]).decode("ascii"))
            elif record_type == "O1":
                o1_records.append(text)
                o1_records_b64.append(base64.b64encode(raw[:read_rc]).decode("ascii"))

    finally:
        if opened:
            try:
                link.JVClose()
            except Exception:
                pass

    total_count = len(raw_records)

    # 厳格な完全性・整合性判定
    if error_msg:
        fetch_status = "FETCH_ERROR"
    elif total_count == 0:
        fetch_status = "NO_RECORDS_FETCHED"
        error_msg = "No race records were returned from JV-Link"
    else:
        if data_spec in ("0B31", "0B30"):
            is_valid = bool(o1_records) and all(
                len(o1.encode("cp932")) == 962 for o1 in o1_records
            )
            validation_msg = (
                "ODDS_ONLY_RECORDS_PRESENT" if is_valid else "O1 records missing or malformed"
            )
        else:
            is_valid, validation_msg = validate_records_integrity(
                ra_records, se_records, o1_records
            )
        if not is_valid:
            fetch_status = "RECORD_INTEGRITY_FAILED"
            error_msg = f"Record integrity check failed: {validation_msg}"
        else:
            fetch_status = "SUCCESS"

    return {
        "fetch_status": fetch_status,
        "error": error_msg,
        "records_count": total_count,
        "ra_count": len(ra_records),
        "se_count": len(se_records),
        "o1_count": len(o1_records),
        "ra_records": ra_records,
        "se_records": se_records,
        "o1_records": o1_records,
        "ra_records_b64": ra_records_b64,
        "se_records_b64": se_records_b64,
        "o1_records_b64": o1_records_b64
    }


def run_worker_capture(
    sid: str,
    data_spec: str,
    from_time: str,
    output_path: str,
    mode: str = "rt",
    key: Optional[str] = None
) -> Dict[str, Any]:
    """
    32bit COM 経由で JV-Link からデータを取得し、JSON ファイルへ安全出力。
    認証状態（auth_status）と取得状態（fetch_status）を独立管理し、
    両方が完全合格の場合のみ success: True とする。
    """
    result: Dict[str, Any] = {
        "success": False,
        "is_32bit": check_is_32bit(),
        "auth_status": "UNVERIFIED",
        "fetch_status": "NOT_STARTED",
        "records_count": 0,
        "ra_count": 0,
        "se_count": 0,
        "o1_count": 0,
        "pid": os.getpid(),
        "timestamp": int(time.time()),
        "sid": sid,
        "data_spec": data_spec,
        "error": None
    }

    if not sid or not sid.strip() or sid[0].isspace():
        result["error"] = "Invalid software ID: empty or leading space (UNKNOWN is valid for development)"
        result["auth_status"] = "SOFTWARE_ID_MISSING"
        _write_output(output_path, result)
        return result

    if not result["is_32bit"]:
        result["error"] = "JV-Link worker requires 32-bit Python"
        result["auth_status"] = "WRONG_PYTHON_ARCHITECTURE"
        _write_output(output_path, result)
        return result

    try:
        import win32com.client
    except ImportError as exc:
        result["error"] = f"pywin32 missing in worker: {exc}"
        result["auth_status"] = "PYWIN32_MISSING"
        _write_output(output_path, result)
        return result

    try:
        link = win32com.client.Dispatch("JVDTLab.JVLink")
    except Exception as exc:
        result["error"] = f"Failed to instantiate JVDTLab.JVLink in worker: {exc}"
        result["auth_status"] = "SDK_NOT_INSTALLED"
        _write_output(output_path, result)
        return result

    # 1. JVInit (戻り値厳密検証)
    try:
        rc = link.JVInit(sid)
        code = int(rc[0]) if isinstance(rc, (tuple, list)) else int(rc)
        if code != 0:
            result["error"] = f"JVInit failed with return code {code}"
            result["auth_status"] = f"INIT_FAILED_CODE_{code}"
            _write_output(output_path, result)
            return result
    except Exception as exc:
        result["error"] = f"JVInit exception: {exc}"
        result["auth_status"] = "INIT_EXCEPTION"
        _write_output(output_path, result)
        return result

    # JVStatus is a download-progress counter, NOT an authentication API.
    # JVInit + a successful open/read are the only evidence available here.
    # Reject the acquisition when the provider returns an error or no data.
    fetch_result = execute_jvlink_fetch(
        link, data_spec=data_spec, from_time=from_time, mode=mode, key=key
    )
    result.update(fetch_result)
    result["success"] = (
        result["fetch_status"] == "SUCCESS"
        and result.get("records_count", 0) > 0
    )
    result["auth_status"] = (
        "AUTHENTICATED" if result["success"] else "UNVERIFIED"
    )

    _write_output(output_path, result)
    return result


def _write_output(path: str, data: dict) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="JV-Link 32-bit Acquisition Worker")
    parser.add_argument("--sid", default="UNKNOWN", help="Software ID")
    parser.add_argument("--spec", default="0B15", help="Data Spec (0B15, 0B30, etc.)")
    parser.add_argument("--from-time", default=datetime.now().strftime("%Y%m%d000000"), help="From timestamp")
    parser.add_argument("--mode", choices=["rt", "stored"], default="rt", help="RT or Stored mode")
    parser.add_argument("--key", help="Key for JVRTOpen")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args(argv)

    res = run_worker_capture(
        sid=args.sid,
        data_spec=args.spec,
        from_time=args.from_time,
        output_path=args.out,
        mode=args.mode,
        key=args.key
    )
    print(json.dumps({
        "success": res.get("success", False),
        "auth_status": res.get("auth_status"),
        "fetch_status": res.get("fetch_status"),
        "records_count": res.get("records_count", 0),
        "error": res.get("error")
    }, ensure_ascii=False))

    sys.exit(0 if res.get("success") else 1)


if __name__ == "__main__":
    main()
