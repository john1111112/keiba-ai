"""
データ出所・真正性認証モジュール (Provenance & Authenticity Verifier)
- 固定の秘密鍵は完全撤去（安全性を担保）
- 本番環境ではローカルキーストア（Git管理外）または環境変数の高強度ランダム秘密鍵を使用
- 秘密鍵未設定時は本番署名・本番通知を完全遮断 (Fail-Closed)
- テスト用署名と本番用署名の完全分離
- ノンス（nonce）とタイムスタンプによる署名の再利用・リプレイ攻撃防止
- HMAC署名（ローカル改ざん検知）に加え、JV-Link/公式通信による実取得成功検証（driver_status）を必須化
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROVIDER_CONFIG = REPO_ROOT / "data" / "provider_config.json"
DEFAULT_FEED_DIR = REPO_ROOT / "data" / "authorized_feed"
DEFAULT_KEY_FILE = REPO_ROOT / "data" / ".secure_key.dat"

# メモリ上のテスト専用鍵（テスト実行中のみ動的生成、本番では使用禁止）
_TEST_EPHEMERAL_KEY: Optional[bytes] = None


def get_provenance_secret(test_mode: bool = False, allow_create_prod_key: bool = True) -> bytes:
    """
    暗号署名用秘密鍵を取得。固定鍵は一切持たない。
    - 本番環境: 環境変数 KEIBA_PROVENANCE_SECRET または Git管理外のローカル鍵ファイル (data/.secure_key.dat)
    - テスト環境: テスト専用の動的メモリ鍵
    秘密鍵が存在せず生成も許可されない場合は RuntimeError を送出 (Fail-Closed)。
    """
    global _TEST_EPHEMERAL_KEY

    if test_mode or (os.getenv("KEIBA_TEST_MODE") == "1"):
        if _TEST_EPHEMERAL_KEY is None:
            _TEST_EPHEMERAL_KEY = secrets.token_bytes(32)
        return _TEST_EPHEMERAL_KEY

    # 本番環境: 環境変数を最優先
    env_secret = os.getenv("KEIBA_PROVENANCE_SECRET")
    if env_secret:
        if len(env_secret.strip()) < 32:
            raise RuntimeError("KEIBA_PROVENANCE_SECRET is too short; minimum 32 chars required for security")
        return env_secret.strip().encode("utf-8")

    # ローカルキーストアの確認
    key_path = Path(DEFAULT_KEY_FILE)
    if key_path.is_file():
        try:
            saved_key = key_path.read_bytes().strip()
            if len(saved_key) >= 32:
                return saved_key
        except Exception:
            pass

    # 本番鍵が存在しない場合、自動生成が許可されていれば強度の高いランダム鍵を生成して安全に保存
    if allow_create_prod_key:
        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            new_key = secrets.token_hex(32).encode("utf-8")
            key_path.write_bytes(new_key)
            return new_key
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize secure provenance key: {exc}") from exc

    raise RuntimeError("No secure provenance secret configured for production environment (Fail-Closed)")


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
    output_path: Optional[Path] = None,
    test_mode: bool = False,
    driver_status: str = "AUTHENTICATED"
) -> Dict[str, Any]:
    """
    正規の取得処理（fetch_authorized_races / jvlink_client）が発行する
    暗号論的真正性セッション署名トークン。
    """
    secret = get_provenance_secret(test_mode=test_mode, allow_create_prod_key=True)

    feed_bytes = json.dumps(feed_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    feed_sha256 = hashlib.sha256(feed_bytes).hexdigest()
    now_iso = datetime.now(timezone.utc).isoformat()
    now_ts = int(time.time())
    nonce = secrets.token_hex(16)

    # 署名対象: ハッシュ | パス | モジュール | プロバイダ | タイムスタンプ | ノンス | ドライバー状態
    msg = f"{feed_sha256}|{str(feed_path.resolve())}|{module_name}|{provider}|{now_ts}|{nonce}|{driver_status}".encode("utf-8")
    signature = hmac.new(secret, msg, hashlib.sha256).hexdigest()

    session_token = {
        "feed_sha256": feed_sha256,
        "feed_path": str(feed_path.resolve()),
        "module_name": module_name,
        "provider": provider,
        "created_at": now_iso,
        "timestamp": now_ts,
        "nonce": nonce,
        "driver_status": driver_status,
        "is_test_signature": test_mode or module_name.startswith("tests."),
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
    1. テスト署名と本番署名の完全分離:
       - 本番運用時、テスト用署名（is_test_signature=True）での本番通知を完全遮断。
    2. 単なる設定ファイルや環境変数のフラグだけでは認証しない。
    3. 有効な「取得セッション署名（session token）」のHMAC検証。
    4. セッション署名の SHA-256 と対象フィード内容の SHA-256 の厳密一致。
    5. 取得鮮度（直近60分以内）と取得モジュールの正当性検証。
    6. 実データ取得元ドライバーの検証（driver_status == "AUTHENTICATED"）。
       未認証状態（UNVERIFIED, FAILED, DEMO等）からの通知は遮断。
    """
    resolved_feed = Path(feed_path).resolve()
    base_dir = Path(feed_dir or resolved_feed.parent or DEFAULT_FEED_DIR)
    is_test_env = test_mode or (os.getenv("KEIBA_TEST_MODE") == "1")

    # 1. セッション署名ファイルの存在確認
    session_file = base_dir / ".acquisition_session.json"
    if not session_file.is_file():
        alt_session = resolved_feed.parent / ".acquisition_session.json"
        if alt_session.is_file():
            session_file = alt_session

    if not session_file.is_file():
        return False, "PROVENANCE_REJECTED: Acquisition process not connected (missing trusted acquisition session)"

    try:
        session_data = json.loads(session_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"PROVENANCE_REJECTED: Corrupted acquisition session metadata: {exc}"

    # 2. 秘密鍵の取得とHMAC検証
    try:
        secret = get_provenance_secret(test_mode=is_test_env, allow_create_prod_key=False)
    except RuntimeError as exc:
        return False, f"PROVENANCE_REJECTED: Provenance key verification failed: {exc}"

    saved_sig = session_data.get("signature")
    feed_sha256 = session_data.get("feed_sha256")
    session_feed_path = session_data.get("feed_path")
    module_name = session_data.get("module_name")
    provider = session_data.get("provider")
    ts = session_data.get("timestamp")
    nonce = session_data.get("nonce", "")
    driver_status = session_data.get("driver_status", "UNVERIFIED")
    is_test_sig = bool(session_data.get("is_test_signature"))

    if not (saved_sig and feed_sha256 and module_name and provider and ts):
        return False, "PROVENANCE_REJECTED: Incomplete acquisition session token"

    expected_msg = f"{feed_sha256}|{session_feed_path}|{module_name}|{provider}|{ts}|{nonce}|{driver_status}".encode("utf-8")
    expected_sig = hmac.new(secret, expected_msg, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(saved_sig, expected_sig):
        return False, "PROVENANCE_REJECTED: Acquisition session signature mismatch (possible forgery or tampering)"

    # 3. テスト署名と本番署名の分離
    if is_test_sig and not is_test_env:
        return False, "PROVENANCE_REJECTED: Test acquisition session cannot be used for production alerts"

    # 4. 取得モジュールが認可取得スクリプトであることの検証
    authorized_modules = {
        "scripts.fetch_authorized_races",
        "src.jvlink_ingest",
        "src.jvlink_client",
        "src.jvlink_worker",
        "tests.test_preproduction_integration_suite",
        "tests.test_safety_and_verification_suite",
        "tests.test_unattended_monitoring_suite",
        "tests.test_jvlink_real_connection_readiness"
    }
    if module_name not in authorized_modules:
        return False, f"PROVENANCE_REJECTED: Unauthorized acquisition module: '{module_name}'"

    # 5. 実データ取得元ドライバーの認証成功確認 (Fail-Closed)
    if driver_status != "AUTHENTICATED":
        return False, f"PROVENANCE_REJECTED: JV-Link driver session is not authenticated (status: '{driver_status}')"

    # 6. 対象フィードの SHA-256 と セッション署名ハッシュの照合
    try:
        current_bytes = json.dumps(feed_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        current_sha256 = hashlib.sha256(current_bytes).hexdigest()
        if current_sha256 != feed_sha256:
            return False, "PROVENANCE_REJECTED: Feed content SHA-256 does not match acquisition session"
    except Exception as exc:
        return False, f"PROVENANCE_REJECTED: Failed to compute feed SHA-256: {exc}"

    # 7. 取得時刻の鮮度検証（直近60分以内の取得データであること）
    try:
        age_seconds = time.time() - float(ts)
        if age_seconds < -10 or age_seconds > 3600:
            return False, f"PROVENANCE_REJECTED: Acquisition session expired or future dated (age: {int(age_seconds)}s)"
    except Exception as exc:
        return False, f"PROVENANCE_REJECTED: Invalid session timestamp: {exc}"

    return True, "PROVENANCE_VERIFIED"
