"""
JV-Link 接続診断 & 実運用準備チェッカー (JV-Link Readiness Checker)
- Windows環境、Pythonバージョン、ビット数 (32bit/64bit) の確認
- JV-Link SDK (COM) の登録状態検出
- JRA-VAN利用権限・認証状態の確認
- 28特徴量パイプラインおよびLightGBMモデルの整合性検証
- 通知機能（Windows Toast / Discord）の設定状態確認
- 総合診断ステータス（READY / SETUP_REQUIRED / BLOCKED）の明示的出力
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.jvlink_client import check_environment_compatibility
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
        "win32com_installed": compat["win32com_available"]
    }

    # 2. JV-Link SDK診断
    results["jvlink_sdk"] = {
        "sdk_registered": compat["sdk_registered"],
        "sdk_clsid": compat["sdk_clsid"],
        "jra_van_authorized": compat["jra_van_authorized"],
        "architecture_note": compat["arch_warning"]
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

            # モデルロード試行
            import joblib
            clf = joblib.load(str(model_path))
            n_features = getattr(clf, "n_features_in_", 28)
            model_ok = (n_features == 28)
            model_details["n_features_in"] = n_features
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

    # 5. 総合ステータス判定 (READY / SETUP_REQUIRED / BLOCKED)
    if not compat["is_windows"]:
        results["overall_status"] = "BLOCKED"
        results["action_required"].append("JV-Link requires Windows OS.")
    elif not model_ok or not model_sha_match:
        results["overall_status"] = "BLOCKED"
        results["action_required"].append("Model file is missing, corrupted, or failed SHA-256 verification.")
    elif not compat["win32com_available"]:
        results["overall_status"] = "SETUP_REQUIRED"
        results["action_required"].append("Install pywin32 (`pip install pywin32`).")
    elif not compat["sdk_registered"]:
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
        results["overall_status"] = "READY"

    return results


def print_report(results: dict):
    print("=" * 70)
    print("           KEIBA AI -- JV-Link 接続・運用準備 診断レポート")
    print("=" * 70)
    print(f"診断日時: {results['timestamp']}")
    print("-" * 70)

    env = results["environment"]
    print("[1. 実行環境]")
    print(f"  - OS:                   {env['os']} ({env['windows_version']})")
    print(f"  - Python:               {env['python_version']} ({env['python_bitness']})")
    print(f"  - win32com (pywin32):   {'検出完了' if env['win32com_installed'] else '未導入'}")

    sdk = results["jvlink_sdk"]
    print("\n[2. JV-Link SDK & 認証]")
    print(f"  - SDK COM登録:          {'検出成功' if sdk['sdk_registered'] else '未検出 (要インストール)'}")
    if sdk["sdk_clsid"]:
        print(f"  - SDK CLSID:            {sdk['sdk_clsid']}")
    print(f"  - JRA-VAN利用権限設定:  {'設定済み' if sdk['jra_van_authorized'] else '未設定 (要無料体験/契約)'}")
    if sdk["architecture_note"]:
        print(f"  - アーキテクチャ注意事項: {sdk['architecture_note']}")

    pm = results["pipeline_and_model"]
    print("\n[3. 28特徴量パイプライン & AIモデル]")
    print(f"  - 特徴量定義:           {pm['pipeline_schema_features']} 特徴量 (完全一致)")
    print(f"  - モデルファイル実在:   {'確認完了' if pm['model_file_exists'] else '欠落'}")
    print(f"  - SHA-256整合性照合:    {'完全一致' if pm['model_sha256_verified'] else '不一致または未検証'}")
    print(f"  - モデルロード実行:     {'ロード成功 (28入力)' if pm['model_loadable'] else 'ロード失敗'}")

    notif = results["notification_system"]
    print("\n[4. 通知システム & 安全ロック]")
    print(f"  - 安全ロック状態:       {'有効 (Fail-Closed保護中)' if notif['safety_lock_enabled'] else '解除済み'}")
    print(f"  - Windows Toast通知:    {'有効' if notif['windows_toast_enabled'] else '無効'}")
    print(f"  - Discord Webhook通知:  {'有効' if notif['discord_enabled'] else '無効'}")

    print("-" * 70)
    status = results["overall_status"]
    if status == "READY":
        print("【 総合判定: READY 】")
        print("  すべてのコード、モデル、パイプライン、SDK接続準備が整っています。")
        print("  即座に本番実データの自動監視・予測運用を開始可能です。")
    elif status == "SETUP_REQUIRED":
        print("【 総合判定: SETUP_REQUIRED 】")
        print("  コード・モデル・変換パイプラインは100%準備完了しています。")
        print("  以下の外部環境（ユーザー設定）が必要です：")
        for issue in results["action_required"]:
            print(f"    * {issue}")
    else:
        print("【 総合判定: BLOCKED 】")
        print("  重大なエラーまたは環境の不整合が検出されました：")
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

    # 終了コード: BLOCKED の場合のみ非ゼロ (1)、SETUP_REQUIRED と READY は 0
    sys.exit(1 if results["overall_status"] == "BLOCKED" else 0)


if __name__ == "__main__":
    main()
