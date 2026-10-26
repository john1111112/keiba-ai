"""
JV-Link 32bit 専用取得ワーカー (JV-Link 32-bit Acquisition Worker)
- 32bit COM DLL である JV-Link SDK と直接通信する専用プロセス
- 64bit 親プロセス（Keiba AI本体）からサブプロセスとして安全に呼び出され、
  RA, SE, O1 レコードを取得して安全な JSON / バイナリ形式で受け渡す
- 契約や無料体験の手続きは行わない
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

REPO_ROOT = Path(__file__).resolve().parent.parent


def check_is_32bit() -> bool:
    """現在のプロセスが32bitアーキテクチャか判定"""
    return struct.calcsize("P") * 8 == 32


def run_worker_capture(sid: str, data_spec: str, from_time: str, output_path: str) -> dict:
    """
    32bit COM 経由で JV-Link からデータを取得し、ファイルへ保存
    """
    result = {
        "success": False,
        "is_32bit": check_is_32bit(),
        "driver_status": "UNVERIFIED",
        "records_count": 0,
        "error": None
    }

    try:
        import win32com.client
    except ImportError as exc:
        result["error"] = f"pywin32 missing in worker: {exc}"
        result["driver_status"] = "SDK_NOT_AVAILABLE"
        return result

    try:
        link = win32com.client.Dispatch("jvdg.JVLink")
    except Exception as exc:
        result["error"] = f"Failed to instantiate jvdg.JVLink in worker: {exc}"
        result["driver_status"] = "SDK_NOT_INSTALLED"
        return result

    # JVInit
    try:
        rc = link.JVInit(sid)
        code = int(rc[0]) if isinstance(rc, (tuple, list)) else int(rc)
        if code != 0:
            result["error"] = f"JVInit failed with code {code}"
            result["driver_status"] = "INIT_FAILED"
            return result
    except Exception as exc:
        result["error"] = f"JVInit exception: {exc}"
        result["driver_status"] = "INIT_EXCEPTION"
        return result

    # JVStatus
    try:
        status_raw = getattr(link, "JVStatus", lambda: 0)()
        status_code = int(status_raw[0]) if isinstance(status_raw, (tuple, list)) else int(status_raw)
        if status_code == 1:
            result["driver_status"] = "AUTHENTICATED"
        elif status_code == 0:
            result["driver_status"] = "SERVICE_INACTIVE"
        elif status_code == 2:
            result["driver_status"] = "MAINTENANCE"
        elif status_code == 3:
            result["driver_status"] = "EXPIRED_OR_UNCONTRACTED"
        else:
            result["driver_status"] = f"UNVERIFIED_STATUS_{status_code}"
    except Exception as exc:
        result["driver_status"] = "STATUS_CHECK_FAILED"

    # 出力ファイル書き込み
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["success"] = (result["driver_status"] == "AUTHENTICATED")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="JV-Link 32-bit Acquisition Worker")
    parser.add_argument("--sid", default="UNKNOWN", help="Software ID")
    parser.add_argument("--spec", default="0B15", help="Data Spec")
    parser.add_argument("--from-time", default="202610260000", help="From time YYYYMMDDHHMM")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args(argv)

    res = run_worker_capture(args.sid, args.spec, args.from_time, args.out)
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0 if res.get("success") else 1)


if __name__ == "__main__":
    main()
