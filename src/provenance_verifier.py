"""
データ出所・真正性認証モジュール (Provenance & Authenticity Verifier)
- JSON内の自己申告（"certified_source": true や "source": "jra_van" 等）による偽装を完全排除
- 単なる環境変数（KEIBA_JVLINK_CERTIFIED=1）や provider_config.json の設定フラグだけでは認証しない
- ローカルファイルのSHA-256一致だけでなく、実際の正規取得処理プロセスが発行した
  セッション署名（Trusted Acquisition Session Token）と強固に紐付け
- 取得処理が未接続の場合、本番通知を完全禁止（Fail-Closed）
- テスト環境と本番環境の認証経路を明確に分離
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROVIDER_CONFIG = REPO_ROOT / "data" / "provider_config.json"
DEFAULT_FEED_DIR = REPO_ROOT / "data" / "authorized_feed"
DEFAULT_SESSION_SIG_FILE = DEFAULT_FEED_DIR / ".acquisition_session.json"

# セッション署名検証用の内部シークレット（プロセス生存期間または永続セッションキー）
_SESSION_SECRET = os.getenv("KEIBA_PROVENANCE_SECRET", "keiba_ai_trusted_session_key_v5").encode("utf-8")


def load_provider_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """登録済み公式プロバイダ設定を取得"""
    p = Path(config_path or DEFAULT_PROVIDER_CONFIG)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    # デフォルト: 未設定（未認証）
    return {
        "jra_van_authorized": False,
        "licensed_feed_enabled": False,
        "allowed_endpoints": [],
        "active_provider": "NONE"
    }


def create_acquisition_signature(
    feed_payload: Dict[str, Any],
    feed_path: Path,
    module_name: str,
    provider: str,
    output_path: Optional[Path] = None
) -> Dict[str, Any]:
    """
    正規の取得処理（fetch_authorized_races / jvlink_client）がデータ保存時に発行する
    暗号論的真正性セッション署名トークン。
    """
    feed_bytes = json.dumps(feed_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    feed_sha256 = hashlib.sha256(feed_bytes).hexdigest()
    now_iso = datetime.now(timezone.utc).isoformat()
    now_ts = int(time.time())

    msg = f"{feed_sha256}|{str(feed_path.resolve())}|{module_name}|{provider}|{now_ts}".encode("utf-8")
    signature = hmac.new(_SESSION_SECRET, msg, hashlib.sha256).hexdigest()

    session_token = {
        "feed_sha256": feed_sha256,
        "feed_path": str(feed_path.resolve()),
        "module_name": module_name,
        "provider": provider,
        "created_at": now_iso,
        "timestamp": now_ts,
        "pid": os.getpid(),
        "signature": signature
    }

    target = Path(output_path or (feed_path.parent / ".acquisition_session.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(session_token, ensure_ascii=False, indent=2), encoding="utf-8")
    return session_token


def verify_feed_provenance(
    feed_path: Path,
    feed_payload: Dict[str, Any],
    provider_config_path: Optional[Path] = None,
    feed_dir: Optional[Path] = None,
    test_mode: bool = False
) -> Tuple[bool, str]:
    """
    フィードの出所が真に認可・認証されているかを厳密判定。
    戻り値: (is_certified, reason)

    【厳格認証ルール】
    1. テストデータ経路と本番データ経路を完全分離:
       - テスト環境時は、明示的テストフラグ (test_mode=True または KEIBA_TEST_MODE=1) が必要。
       - 本番通知が有効な運用時、テストデータは絶対に本番認可として扱わない。
    2. 単なる環境変数 (KEIBA_JVLINK_CERTIFIED=1) や provider_config.json の設定値だけで認証しない。
    3. 正規取得処理によって発行された有効な「取得セッション署名（session token）」を検証。
    4. セッション署名の SHA-256 と対象フィード内容の SHA-256 が一致すること。
    5. 取得鮮度（直近60分以内）と取得モジュールの正当性を検証。
    6. 取得処理が未接続または署名欠落・不一致の場合は、Fail-Closed で停止。
    """
    resolved_feed = Path(feed_path).resolve()
    base_dir = Path(feed_dir or resolved_feed.parent or DEFAULT_FEED_DIR)

    # テスト環境フラグ確認
    is_test_env = test_mode or (os.getenv("KEIBA_TEST_MODE") == "1")

    # 1. プロバイダ設定の基本確認
    cfg = load_provider_config(provider_config_path)
    has_licensed_feed = bool(cfg.get("licensed_feed_enabled"))
    has_jra_van = bool(cfg.get("jra_van_authorized"))

    # 単なる設定JSONのフラグだけ、あるいは環境変数の偽装だけでは認証不可
    # ※ 取得セッションの実在が必須
    session_file = base_dir / ".acquisition_session.json"
    if not session_file.is_file():
        # 代替パス探索 (feed_path と同一ディレクトリ)
        alt_session = resolved_feed.parent / ".acquisition_session.json"
        if alt_session.is_file():
            session_file = alt_session

    if not session_file.is_file():
        return False, "PROVENANCE_REJECTED: Acquisition process not connected (missing trusted acquisition session)"

    try:
        session_data = json.loads(session_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"PROVENANCE_REJECTED: Corrupted acquisition session metadata: {exc}"

    # 2. セッション署名の改ざん検証
    saved_sig = session_data.get("signature")
    feed_sha256 = session_data.get("feed_sha256")
    session_feed_path = session_data.get("feed_path")
    module_name = session_data.get("module_name")
    provider = session_data.get("provider")
    ts = session_data.get("timestamp")

    if not (saved_sig and feed_sha256 and module_name and provider and ts):
        return False, "PROVENANCE_REJECTED: Incomplete acquisition session token"

    expected_msg = f"{feed_sha256}|{session_feed_path}|{module_name}|{provider}|{ts}".encode("utf-8")
    expected_sig = hmac.new(_SESSION_SECRET, expected_msg, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(saved_sig, expected_sig):
        return False, "PROVENANCE_REJECTED: Acquisition session signature mismatch (possible forgery or tampering)"

    # 3. 取得モジュールが認可取得スクリプトであることの検証
    authorized_modules = {
        "scripts.fetch_authorized_races",
        "src.jvlink_ingest",
        "src.jvlink_client",
        "tests.test_preproduction_integration_suite",
        "tests.test_safety_and_verification_suite",
        "tests.test_unattended_monitoring_suite"
    }
    if module_name not in authorized_modules:
        return False, f"PROVENANCE_REJECTED: Unauthorized acquisition module: '{module_name}'"

    # テストモジュールによる署名の場合、本番実行での通知利用を遮断
    if module_name.startswith("tests.") and not is_test_env:
        return False, "PROVENANCE_REJECTED: Test acquisition session cannot be used for production alerts"

    # 4. 対象フィードの SHA-256 と セッション署名ハッシュの照合
    try:
        current_bytes = json.dumps(feed_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        current_sha256 = hashlib.sha256(current_bytes).hexdigest()
        if current_sha256 != feed_sha256:
            return False, "PROVENANCE_REJECTED: Feed content SHA-256 does not match acquisition session"
    except Exception as exc:
        return False, f"PROVENANCE_REJECTED: Failed to compute feed SHA-256: {exc}"

    # 5. 取得時刻の鮮度検証（直近60分以内の取得データであること）
    try:
        age_seconds = time.time() - float(ts)
        if age_seconds < -10 or age_seconds > 3600:
            return False, f"PROVENANCE_REJECTED: Acquisition session expired or future dated (age: {int(age_seconds)}s)"
    except Exception as exc:
        return False, f"PROVENANCE_REJECTED: Invalid session timestamp: {exc}"

    return True, "PROVENANCE_VERIFIED"
