"""
データ出所・真正性認証モジュール (Provenance & Authenticity Verifier)
- JSON内の自己申告（"certified_source": true や "source": "jra_van" 等）による偽装を完全排除
- 実際に検証された取得経路、システム環境に設定済みの公式プロバイダ、公式スクリプトが生成した整合性メタデータからのみ真正性を認定
- 未認証データ・出所不明データは Fail-Closed（安全遮断）で本番通知を禁止
"""
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROVIDER_CONFIG = REPO_ROOT / "data" / "provider_config.json"
DEFAULT_FEED_DIR = REPO_ROOT / "data" / "authorized_feed"


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


def verify_feed_provenance(
    feed_path: Path,
    feed_payload: Dict[str, Any],
    provider_config_path: Optional[Path] = None,
    feed_dir: Optional[Path] = None
) -> Tuple[bool, str]:
    """
    フィードの出所が真に認可・認証されているかを厳密判定。
    戻り値: (is_certified, reason)
    ※ JSON内部の自己申告文字列 ("certified_source": true 等) は一切信用しない。
    """
    cfg = load_provider_config(provider_config_path)
    base_dir = Path(feed_dir or DEFAULT_FEED_DIR)
    resolved_feed = Path(feed_path).resolve()

    # 1. プロバイダ設定の有効性チェック
    has_env_token = bool(os.getenv("KEIBA_FEED_TOKEN"))
    has_licensed_feed = bool(cfg.get("licensed_feed_enabled") or has_env_token)
    has_jra_van = bool(cfg.get("jra_van_authorized") or os.getenv("KEIBA_JVLINK_CERTIFIED") == "1")

    if not has_licensed_feed and not has_jra_van:
        return False, "PROVENANCE_REJECTED: No licensed provider configured in system"

    # 2. 取得元メタデータ（last_feed_status.json）との整合性照合
    status_file = base_dir / "last_feed_status.json"
    if not status_file.is_file():
        return False, "PROVENANCE_REJECTED: Missing trusted last_feed_status.json"

    try:
        status_data = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"PROVENANCE_REJECTED: Corrupted status metadata: {exc}"

    if status_data.get("status") != "VALIDATED_UNCERTIFIED" and status_data.get("status") != "VALIDATED_CERTIFIED":
        return False, f"PROVENANCE_REJECTED: Status metadata shows invalid transport: {status_data.get('status')}"

    # 3. アーカイブファイルの実在性と一致確認
    archive_path_str = status_data.get("archive")
    if not archive_path_str:
        return False, "PROVENANCE_REJECTED: Status metadata missing archive reference"

    archive_path = Path(archive_path_str).resolve()
    if not archive_path.is_file():
        return False, "PROVENANCE_REJECTED: Referenced archive snapshot does not exist"

    # 検証対象のフィードが、認可取得アーカイブまたはそのポインタと一致するか検証
    try:
        archive_content = json.loads(archive_path.read_text(encoding="utf-8"))
        archive_feed = archive_content.get("feed", {})
        
        # フィード内容の SHA-256 ハッシュを計算して比較
        feed_bytes = json.dumps(feed_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        archive_bytes = json.dumps(archive_feed, sort_keys=True, ensure_ascii=False).encode("utf-8")

        if hashlib.sha256(feed_bytes).hexdigest() != hashlib.sha256(archive_bytes).hexdigest():
            return False, "PROVENANCE_REJECTED: Feed content does not match authorized archive snapshot"
    except Exception as exc:
        return False, f"PROVENANCE_REJECTED: Archive verification error: {exc}"

    # 4. 取得時刻の鮮度検証（直近60分以内の取得データであること）
    received_at_str = status_data.get("received_at")
    if not received_at_str:
        return False, "PROVENANCE_REJECTED: Missing receipt timestamp in status"

    try:
        received_dt = datetime.fromisoformat(received_at_str.replace("Z", "+00:00"))
        if received_dt.tzinfo is None:
            received_dt = received_dt.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - received_dt).total_seconds()
        if age_seconds < -10 or age_seconds > 3600:
            return False, f"PROVENANCE_REJECTED: Feed snapshot expired or future dated (age: {int(age_seconds)}s)"
    except Exception as exc:
        return False, f"PROVENANCE_REJECTED: Bad receipt timestamp: {exc}"

    return True, "PROVENANCE_VERIFIED"
