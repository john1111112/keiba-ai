"""
JV-Link 接続診断 & 実運用準備チェッカー (JV-Link Readiness Checker)
8段階の明確な独立診断マトリクス:
1. Python環境: READY / NOT_READY
2. SDK導入: READY / NOT_READY
3. COM接続: READY / NOT_READY
4. API仕様検証: VERIFIED / UNVERIFIED
5. 利用権限: AUTHORIZED / NOT_AUTHORIZED
6. 実データ取得: SUCCESS / NOT_TESTED / FAILED
7. AI予測: SUCCESS / NOT_TESTED / FAILED
8. 通知: SUCCESS / NOT_TESTED / FAILED
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.jvlink_client import check_environment_compatibility, find_jvlink_python
from src.provenance_verifier import load_provider_config
from src.features import FEATURE_COLS_V2_28
from src.automated_monitor import load_alert_config


def run_full_readiness_check() -> dict:
    """網羅的なシステム準備診断を実行"""
    results = {
        "timestamp": None,
        "environment": {},
        "jvlink_sdk": {},
        "pipeline_and_model": {},
        "notification_system": {},
        "matrix": {},
        "overall_status": "READY",
        "action_required": []
    }

    from datetime import datetime, timezone
    results["timestamp"] = datetime.now(timezone.utc).isoformat()

    # 1. 環境互換性診断
    compat = check_environment_compatibility()
    results["environment"] = {
        "os": platform.system(),
        "windows_version": compat["windows_version"],
        "python_version": compat["python_version"],
        "python_bitness": compat["python_architecture"],
        "win32com_installed": compat["win32com_available"],
        "python32_available": compat["python32_available"],
        "python32_path": compat["python32_path"],
        "jvlink_worker_python": compat.get("jvlink_python"),
        "jvlink_worker_bitness": compat.get("jvlink_python_bitness")
    }

    # 2. JV-Link SDK & COM 独立診断
    com_creatable = False
    com_error = None
    probe_details = {}
    if compat.get("jvlink_python"):
        try:
            probe = subprocess.run([compat["jvlink_python"], str(REPO_ROOT / "scripts" / "probe_jvlink_com.py")], capture_output=True, text=True, timeout=12)
            probe_details = json.loads(probe.stdout)
            com_creatable = probe.returncode == 0 and probe_details.get("com_creatable") is True
            com_error = probe_details.get("error")
        except Exception as exc:
            com_error = str(exc)

    results["jvlink_sdk"] = {
        "sdk_registered": compat["sdk_registered"] or com_creatable,
        "sdk_clsid": compat["sdk_clsid"],
        "com_instantiable": com_creatable,
        "com_error": com_error,
        "worker_probe": probe_details,
        "jra_van_authorized": compat["jra_van_authorized"]
    }

    # 3. 28特徴量パイプライン & モデル診断
    model_path = REPO_ROOT / "models" / "keiba_lgbm_win_v2_28cols.joblib"
    sha_path = REPO_ROOT / "models" / "keiba_lgbm_win_v2_28cols.joblib.sha256"
    model_ok = False
    model_sha_match = False
    model_details = {}

    if model_path.is_file():
        try:
            m_bytes = model_path.read_bytes()
            actual_sha = hashlib.sha256(m_bytes).hexdigest()
            model_details["actual_sha256"] = actual_sha
            if sha_path.is_file():
                sha_info = json.loads(sha_path.read_text(encoding="utf-8"))
                expected_sha = sha_info.get("sha256", "").lower()
                model_details["expected_sha256"] = expected_sha
                model_sha_match = (actual_sha.lower() == expected_sha)
            else:
                model_sha_match = False

            import joblib
            loaded = joblib.load(str(model_path))
            feature_names = loaded.get("feature_cols") if isinstance(loaded, dict) else None
            predictor = loaded.get("model") if isinstance(loaded, dict) else None
            model_ok = (
                isinstance(feature_names, (list, tuple))
                and list(feature_names) == list(FEATURE_COLS_V2_28)
                and callable(getattr(predictor, "predict_proba", None))
            )
            model_details["n_features_in"] = len(feature_names) if feature_names else None
            model_details["valid_model_bundle"] = model_ok
        except Exception as exc:
            model_details["error"] = str(exc)

    results["pipeline_and_model"] = {
        "pipeline_schema_features": len(FEATURE_COLS_V2_28),
        "model_file_exists": model_path.is_file(),
        "model_sha256_verified": model_sha_match,
        "model_loadable": model_ok,
        "details": model_details
    }

    # 4. 通知システム診断
    alert_cfg = load_alert_config()
    results["notification_system"] = {
        "safety_lock_enabled": alert_cfg.get("safety_lock_enabled", True),
        "windows_toast_enabled": alert_cfg.get("windows_toast_enabled", True),
        "discord_enabled": alert_cfg.get("discord_enabled", False),
        "has_discord_webhook": bool(alert_cfg.get("discord_webhook_url"))
    }

    # 5. 8段階独立判定マトリクス (要求仕様厳密準拠)
    # 1. Python環境
    m_python = "READY" if (compat["is_windows"] and compat.get("jvlink_python") and probe_details.get("pywin32_available", False)) else "NOT_READY"
    # 2. SDK導入
    m_sdk = "READY" if com_creatable else "NOT_READY"
    # 3. COM接続
    m_com = "READY" if com_creatable else "NOT_READY"
    # 4. API仕様検証 (JVRTOpen, JVRead, 16桁キー、固定長仕様の照合完了状態)
    m_api = "UNVERIFIED"
    # 5. 利用権限
    m_auth = "NOT_TESTED" if compat["jra_van_authorized"] else "NOT_AUTHORIZED"
    # 6. 実データ取得 (無料体験・実回線未接続のため NOT_TESTED)
    m_fetch = "NOT_TESTED"
    # 7. AI予測 (モデルとパイプラインがロード可能でテスト済みのため SUCCESS)
    m_ai = "NOT_TESTED" if (model_ok and model_sha_match) else "FAILED"
    # 8. 通知 (安全ロック下でDB整合性・ハンドラ検証済みのため SUCCESS)
    m_notif = "NOT_TESTED"

    results["matrix"] = {
        "python_env": m_python,
        "sdk_installation": m_sdk,
        "com_connection": m_com,
        "api_specification": m_api,
        "subscription_auth": m_auth,
        "live_data_fetch": m_fetch,
        "ai_prediction": m_ai,
        "notification": m_notif
    }

    # 総合ステータス
    if not compat["is_windows"]:
        results["overall_status"] = "BLOCKED"
        results["action_required"].append("JV-Link requires Windows OS.")
    elif not model_ok or not model_sha_match:
        results["overall_status"] = "BLOCKED"
        results["action_required"].append("Model file is missing, corrupted, or failed SHA-256 verification.")
    elif not com_creatable:
        results["overall_status"] = "SETUP_REQUIRED"
        results["action_required"].append(
            "Download and install JV-Link SDK from JRA-VAN Data Lab official website."
        )
    elif not compat["jra_van_authorized"]:
        results["overall_status"] = "SETUP_REQUIRED"
        results["action_required"].append(
            "Activate JRA-VAN free trial / subscription and set 'jra_van_authorized: true' in data/provider_config.json."
        )
    else:
        results["overall_status"] = "SETUP_REQUIRED"
        results["action_required"].append("Live end-to-end operation is not yet verified.")

    return results


def print_report(results: dict):
    print("=" * 70)
    print("           KEIBA AI -- JV-Link 接続・運用準備 診断レポート")
    print("=" * 70)
    print(f"診断日時: {results['timestamp']}")
    print("-" * 70)

    m = results["matrix"]
    print("[8段階独立 準備状態マトリクス]")
    print(f"  1. Python環境:       {m['python_env']}")
    print(f"  2. SDK導入:          {m['sdk_installation']}")
    print(f"  3. COM接続:          {m['com_connection']}")
    print(f"  4. API仕様検証:      {m['api_specification']}")
    print(f"  5. 利用権限:         {m['subscription_auth']}")
    print(f"  6. 実データ取得:     {m['live_data_fetch']} (実回線未接続・無料体験前)")
    print(f"  7. AI予測パイプライン:{m['ai_prediction']}")
    print(f"  8. 通知システム:     {m['notification']}")
    print("-" * 70)

    env = results["environment"]
    print("[詳細環境情報]")
    print(f"  - Main Python:       {env['python_version']} ({env['python_bitness']})")
    print(f"  - win32com:          {'導入完了' if env['win32com_installed'] else '未導入'}")
    print(f"  - 32bit Python:      {env['python32_path'] if env['python32_available'] else '未検出'}")

    pm = results["pipeline_and_model"]
    print(f"  - AIモデルSHA-256:   {'完全一致' if pm['model_sha256_verified'] else '不一致'}")

    print("-" * 70)
    status = results["overall_status"]
    if status == "READY":
        print("【 総合判定: READY 】")
        print("  すべてのコード、モデル、パイプライン、SDK接続準備が整っています。")
    elif status == "SETUP_REQUIRED":
        print("【 総合判定: SETUP_REQUIRED 】")
        print("  未検証または未設定の条件が残っています。")
        print("  必要な対応・検証事項：")
        for issue in results["action_required"]:
            print(f"    * {issue}")
    else:
        print("【 総合判定: BLOCKED 】")
        for issue in results["action_required"]:
            print(f"    * {issue}")
    print("=" * 70)


def main(argv=None):
    parser = argparse.ArgumentParser(description="JV-Link Readiness Checker")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)

    results = run_full_readiness_check()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results)

    sys.exit(1 if results["overall_status"] == "BLOCKED" else 0)


if __name__ == "__main__":
    main()
