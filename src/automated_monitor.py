"""
バックグラウンド自動監視・AI予測・通知統合エンジン (Automated Monitor Engine)
- アプリを閉じていても、Windowsタスクまたは常駐プロセスから定期実行可能
- 認可された発走前レースをスキャンし、LightGBM 28特徴量で単勝勝率を予測
- 期待回収倍率 (EV = 勝率 × 単勝オッズ) を算出し、閾値判定
- 重複通知防止 (Idempotency) を保証し、Windowsトースト通知およびDiscord通知を送信
- 取得失敗・モデル異常・通信失敗をログに記録し、次回サイクルで自動復旧
- 本番通知の安全ロック (safety_lock_enabled) を徹底し、自己申告値の偽装を完全排除した正規データ認証を適用
- 安全ロック解除後の正常再評価および通信失敗時の安全再試行（リトライ）を保証
"""
from __future__ import annotations
import json
import logging
import math
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd

from src.model import KeibaAIWinModel, MODEL_WIN_V2_28_PATH
from src.prerace_feature_pipeline import (
    build_prerace_28features,
    validate_clean_prerace_input,
    validate_feature_alignment
)
from src.notifier import send_windows_notification, get_discord_webhook
from src.provenance_verifier import verify_feed_provenance, load_provider_config

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALERT_CONFIG = REPO_ROOT / "data" / "alert_config.json"
DEFAULT_STATE_DB = REPO_ROOT / "data" / "alert_state.db"
DEFAULT_LOG_PATH = REPO_ROOT / "data" / "background_monitor.log"
DEFAULT_FEED_DIR = REPO_ROOT / "data" / "authorized_feed"


def get_logger(log_path: Optional[Path] = None) -> logging.Logger:
    logger = logging.getLogger("KeibaAIMonitor")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        target = Path(log_path or DEFAULT_LOG_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(target, encoding="utf-8")
        fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


def load_alert_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """アラート設定（閾値、通知チャネル、安全ロック）を読み込む。デフォルトで安全ロックON"""
    p = Path(config_path or DEFAULT_ALERT_CONFIG)
    default_config = {
        "min_ev": 1.15,
        "min_win_prob": 0.20,
        "max_odds_age_seconds": 120,
        "min_seconds_to_start": 300,
        "max_seconds_to_start": 1800,
        "windows_toast_enabled": True,
        "discord_enabled": False,
        "safety_lock_enabled": True,  # 安全第一：正規データ検証完了まで本番通知をロック（デフォルトTrue）
        "max_retries": 3,
    }
    if p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                default_config.update(loaded)
        except Exception:
            pass
    return default_config


def save_alert_config(config: Dict[str, Any], config_path: Optional[Path] = None) -> None:
    """アラート設定を保存する"""
    p = Path(config_path or DEFAULT_ALERT_CONFIG)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def init_alert_db(db_path: Path) -> None:
    """通知履歴テーブルを初期化（WALモード＆busy_timeout設定による同時アクセス安全性保証）"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_delivery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id TEXT NOT NULL,
            horse_number INTEGER NOT NULL,
            horse_name TEXT,
            event_type TEXT NOT NULL,
            win_odds REAL NOT NULL,
            win_probability REAL NOT NULL,
            expected_value REAL NOT NULL,
            first_sent_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            delivery_status TEXT NOT NULL,
            windows_status TEXT DEFAULT 'NOT_SENT',
            discord_status TEXT DEFAULT 'NOT_SENT',
            retry_count INTEGER DEFAULT 0,
            error_message TEXT,
            UNIQUE(race_id, horse_number, event_type)
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_delivery_race ON alert_delivery (race_id, horse_number);")


def is_already_delivered(
    db_path: Path,
    race_id: str,
    horse_number: int,
    event_type: str,
    safety_lock_active: bool = False,
    max_retries: int = 3
) -> bool:
    """
    重複通知防止チェック
    - DELIVERED または PARTIAL_SUCCESS: 既に配信完了のため送信抑止
    - SKIPPED_BY_SAFETY_LOCK:
        - 安全ロック継続中ならスキップ継続
        - 安全ロック解除後（正規認証接続時）は、再評価・送信を許可（Falseを返す）
    - FAILED:
        - retry_count < max_retries であれば再試行可能（Falseを返す）
        - retry_count >= max_retries であれば再試行打ち切り（Trueを返す）
    """
    if not db_path.exists():
        return False
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=30)) as conn:
        row = conn.execute(
            "SELECT delivery_status, retry_count FROM alert_delivery WHERE race_id = ? AND horse_number = ? AND event_type = ?",
            (str(race_id), int(horse_number), str(event_type))
        ).fetchone()
        if row is None:
            return False
        status, retries = row[0], (row[1] or 0)
        if status in ("DELIVERED", "PARTIAL_SUCCESS"):
            return True
        if status == "SKIPPED_BY_SAFETY_LOCK":
            # 安全ロック中なら再送不要（既に安全スキップ記録あり）
            # ロック解除後なら再送を許可
            return safety_lock_active
        if status == "FAILED":
            return retries >= max_retries
        return status in ("CLAIMED",)


def try_claim_alert(db_path: Path, race_id: str, horse_number: int, event_type: str) -> bool:
    """アトミックに送信権を獲得（同時実行時の二重送信を物理的に排除）"""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with closing(sqlite3.connect(db_path, timeout=10)) as conn, conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT OR IGNORE INTO alert_delivery (
                race_id, horse_number, horse_name, event_type, win_odds, win_probability,
                expected_value, first_sent_at, channel, delivery_status,
                windows_status, discord_status, retry_count, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """, (
                str(race_id), int(horse_number), "", str(event_type),
                0.0, 0.0, 0.0, now_iso, "claim", "CLAIMED",
                "PENDING", "PENDING", ""
            ))
            if cur.rowcount == 1:
                return True
            # 既存行がある場合、FAILED または SKIPPED からの再試行・昇格クレームか判定
            row = cur.execute(
                "SELECT delivery_status, retry_count FROM alert_delivery WHERE race_id = ? AND horse_number = ? AND event_type = ?",
                (str(race_id), int(horse_number), str(event_type))
            ).fetchone()
            if row and row[0] in ("FAILED", "SKIPPED_BY_SAFETY_LOCK"):
                cur.execute("""
                UPDATE alert_delivery
                SET delivery_status = 'CLAIMED_RETRY'
                WHERE race_id = ? AND horse_number = ? AND event_type = ? AND delivery_status IN ('FAILED', 'SKIPPED_BY_SAFETY_LOCK')
                """, (str(race_id), int(horse_number), str(event_type)))
                return cur.rowcount == 1
            return False
    except Exception:
        return False


def record_alert_delivery(
    db_path: Path,
    race_id: str,
    horse_number: int,
    horse_name: str,
    event_type: str,
    win_odds: float,
    win_probability: float,
    expected_value: float,
    channel: str,
    status: str,
    windows_status: str = "NOT_SENT",
    discord_status: str = "NOT_SENT",
    error: str = ""
) -> None:
    """通知履歴をSQLiteに安全に保存（成否の個別記録およびリトライカウント更新）"""
    now_iso = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
        # 既存行の retry_count を取得
        row = conn.execute(
            "SELECT retry_count FROM alert_delivery WHERE race_id = ? AND horse_number = ? AND event_type = ?",
            (str(race_id), int(horse_number), str(event_type))
        ).fetchone()
        cur_retries = (row[0] if row else 0)
        new_retries = cur_retries + 1 if status == "FAILED" else cur_retries

        conn.execute("""
        INSERT OR REPLACE INTO alert_delivery (
            race_id, horse_number, horse_name, event_type, win_odds, win_probability,
            expected_value, first_sent_at, channel, delivery_status,
            windows_status, discord_status, retry_count, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(race_id), int(horse_number), str(horse_name), str(event_type),
            float(win_odds), float(win_probability), float(expected_value),
            now_iso, str(channel), str(status),
            str(windows_status), str(discord_status), new_retries, str(error)
        ))


def resolve_latest_authorized_feed(feed_dir: Path) -> Tuple[Optional[Path], str]:
    """
    データ取得スクリプトが保存したスナップショットから最新の有効フィードを安全に特定
    戻り値: (feed_path, status_reason)
    """
    base_dir = Path(feed_dir)
    status_file = base_dir / "last_feed_status.json"
    if not status_file.is_file():
        # latest.json が直接存在する場合はフォールバック
        latest_file = base_dir / "latest.json"
        if latest_file.is_file():
            return latest_file, "USING_DIRECT_LATEST_FILE"
        return None, "NO_FEED_AVAILABLE"

    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        archive_str = data.get("archive")
        if not archive_str:
            return None, "NO_ARCHIVE_IN_STATUS"
        archive_path = Path(archive_str)
        if not archive_path.is_file():
            return None, "ARCHIVE_FILE_MISSING"

        # 鮮度確認（60分以内）
        rcv_str = data.get("received_at")
        if rcv_str:
            rcv_dt = datetime.fromisoformat(rcv_str.replace("Z", "+00:00"))
            if rcv_dt.tzinfo is None:
                rcv_dt = rcv_dt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - rcv_dt).total_seconds() > 3600:
                return None, "FEED_EXPIRED"

        return archive_path, "RESOLVED_FROM_STATUS"
    except Exception as exc:
        return None, f"FEED_RESOLUTION_ERROR: {exc}"


def validate_feed_payload(
    payload: Any,
    received_at: datetime,
    feed_path: Optional[Path] = None,
    provider_config_path: Optional[Path] = None
) -> Tuple[bool, str, List[Dict[str, Any]], bool]:
    """
    認可フィードの形式・公開日時・オッズ観測日時・出所認証を厳格に検証
    ※ JSON自己申告値 ("certified_source": true 等) による偽装を排除し、
       システムプロバイダ設定およびアーカイブ真正性検証を通して判定。
    戻り値: (is_valid, reason, races, is_certified_source)
    """
    if not isinstance(payload, dict):
        return False, "Payload must be a dictionary", [], False

    # 包装フォーマット（{"received_at": ..., "feed": {...}}）への対応
    feed_body = payload.get("feed", payload) if isinstance(payload, dict) else payload
    if not isinstance(feed_body, dict):
        return False, "Invalid feed envelope structure", [], False

    # 出所真正性の検証（自己申告文字列は信用せず、システム設定とアーカイブ整合性で検証）
    is_certified = False
    if feed_path:
        is_cert, cert_reason = verify_feed_provenance(
            feed_path=Path(feed_path),
            feed_payload=feed_body,
            provider_config_path=provider_config_path
        )
        is_certified = is_cert

    # 公開日時の検証
    pub_str = feed_body.get("published_at")
    if not pub_str:
        return False, "Missing published_at in feed", [], False
    try:
        pub_dt = datetime.fromisoformat(str(pub_str).replace("Z", "+00:00"))
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
    except Exception as exc:
        return False, f"Invalid published_at format: {exc}", [], False

    if pub_dt > received_at:
        return False, "Future publication date detected", [], False

    raw_races = feed_body.get("races")
    if not isinstance(raw_races, list):
        return False, "Payload races field must be a list", [], False

    valid_races = []
    seen_race_ids = set()

    for idx, r in enumerate(raw_races):
        if not isinstance(r, dict):
            return False, f"Race at index {idx} must be a dictionary", [], False

        rid = str(r.get("race_id", "")).strip()
        if not rid or rid in seen_race_ids:
            return False, f"Invalid or duplicate race_id '{rid}' at index {idx}", [], False
        seen_race_ids.add(rid)

        # 発走時刻の検証
        time_str = r.get("race_time") or r.get("start_time")
        if not time_str:
            return False, f"Missing race_time for race {rid}", [], False
        try:
            start_dt = datetime.fromisoformat(str(time_str).replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        except Exception as exc:
            return False, f"Invalid race_time for race {rid}: {exc}", [], False

        # 発走済みレースは除外
        status = r.get("race_status", "upcoming")
        if status != "upcoming" or start_dt <= received_at:
            continue

        horses = r.get("horses", [])
        if not isinstance(horses, list) or len(horses) == 0:
            return False, f"Race {rid} has no valid horses list", [], False

        seen_hnums = set()
        for h in horses:
            if not isinstance(h, dict):
                return False, f"Race {rid} contains non-dict horse", [], False
            hnum = h.get("horse_number")
            if type(hnum) is not int or hnum < 1 or hnum in seen_hnums:
                return False, f"Race {rid} invalid or duplicate horse_number {hnum}", [], False
            seen_hnums.add(hnum)

            odds = h.get("win_odds")
            if odds is not None:
                try:
                    f_odds = float(odds)
                    if not math.isfinite(f_odds) or f_odds < 1.0:
                        return False, f"Race {rid} horse {hnum} invalid odds {odds}", [], False
                except (ValueError, TypeError):
                    return False, f"Race {rid} horse {hnum} non-numeric odds {odds}", [], False

                obs_str = h.get("odds_observed_at")
                if obs_str:
                    try:
                        obs_dt = datetime.fromisoformat(str(obs_str).replace("Z", "+00:00"))
                        if obs_dt.tzinfo is None:
                            obs_dt = obs_dt.replace(tzinfo=timezone.utc)
                        if obs_dt > received_at or obs_dt > pub_dt or obs_dt >= start_dt:
                            return False, f"Race {rid} horse {hnum} invalid odds observation timestamp", [], False
                    except Exception as exc:
                        return False, f"Race {rid} horse {hnum} bad odds timestamp: {exc}", [], False

        # 開催日の確定
        if "race_date" not in r:
            r["race_date"] = start_dt.strftime("%Y-%m-%d")

        valid_races.append(r)

    return True, "VALIDATED", valid_races, is_certified


def validate_fresh_race_odds(
    horses: List[Dict[str, Any]], now_dt: datetime, max_age_seconds: int
) -> Tuple[bool, str]:
    """Do not use unobserved, stale or future-dated odds for notifications.

    A numerical quote without a trustworthy timestamp is not sufficient.
    This is stricter than the read-only dashboard, which may still display
    win probabilities when some current odds are unavailable.
    """
    if max_age_seconds <= 0 or now_dt.tzinfo is None:
        return False, "INVALID_ODDS_FRESHNESS_CONFIG"
    for horse in horses:
        odds = horse.get("win_odds")
        observed = horse.get("odds_observed_at")
        if odds is None or not observed:
            return False, "MISSING_WIN_ODDS_OR_OBSERVATION_TIME"
        try:
            price = float(odds)
            measured_at = datetime.fromisoformat(
                str(observed).replace("Z", "+00:00")
            )
            if not math.isfinite(price) or price < 1.0 or measured_at.tzinfo is None:
                return False, "INVALID_WIN_ODDS_OR_TIMESTAMP"
            age = (now_dt - measured_at).total_seconds()
            if age < 0 or age > max_age_seconds:
                return False, "STALE_OR_FUTURE_WIN_ODDS"
        except (ValueError, TypeError, OverflowError):
            return False, "INVALID_WIN_ODDS_OR_TIMESTAMP"
    return True, "FRESH_ODDS_VERIFIED"


def collect_fresh_win_odds(
    horses: List[Dict[str, Any]], now_dt: datetime, max_age_seconds: int
) -> Dict[int, float]:
    """A missing quote on another horse must not block a valid buy signal.

    Every quote that may trigger a notification is independently checked for
    source timestamps and freshness. Predictions still cover all runners.
    """
    quotes: Dict[int, float] = {}
    for horse in horses:
        valid, _ = validate_fresh_race_odds([horse], now_dt, max_age_seconds)
        if valid:
            quotes[int(horse["horse_number"])] = float(horse["win_odds"])
    return quotes


def run_monitor_scan(
    feed_path: Path,
    state_db_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    as_of_time: Optional[str] = None,
    model_path: Optional[Path] = None,
    past_db_path: Optional[Path] = None,
    provider_config_path: Optional[Path] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    発走前レースの自動スキャン・特徴量生成・AI予測・期待値判定・自動通知の統合実行
    """
    logger = get_logger()
    state_db = Path(state_db_path or DEFAULT_STATE_DB)
    init_alert_db(state_db)
    config = load_alert_config(config_path)

    now_dt = datetime.fromisoformat(as_of_time.replace("Z", "+00:00")) if as_of_time else datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    feed_file = Path(feed_path)
    if not feed_file.is_file():
        msg = f"認可フィードが存在しません: {feed_file}"
        logger.info(msg)
        return {
            "status": "NO_FEED_FILE",
            "message": msg,
            "processed_races": 0,
            "model_inference_allowed": False,
            "candidates_found": 0,
            "notifications_sent": 0,
            "checked_at": now_dt.isoformat()
        }

    # フィード読込と厳格な統一検証
    try:
        raw_feed = json.loads(feed_file.read_text(encoding="utf-8"))
    except Exception as exc:
        msg = f"フィードJSON解析エラー: {exc}"
        logger.error(msg)
        return {
            "status": "FEED_PARSE_ERROR",
            "message": msg,
            "processed_races": 0,
            "model_inference_allowed": False,
            "candidates_found": 0,
            "notifications_sent": 0,
            "checked_at": now_dt.isoformat()
        }

    is_valid, validation_reason, races, is_certified_source = validate_feed_payload(
        raw_feed,
        now_dt,
        feed_path=feed_file,
        provider_config_path=provider_config_path
    )
    if not is_valid:
        msg = f"フィード検証失敗（拒絶）: {validation_reason}"
        logger.warning(msg)
        return {
            "status": "FEED_VALIDATION_REJECTED",
            "message": msg,
            "processed_races": 0,
            "model_inference_allowed": False,
            "candidates_found": 0,
            "notifications_sent": 0,
            "checked_at": now_dt.isoformat()
        }

    # モデルの準備
    m_path = Path(model_path or MODEL_WIN_V2_28_PATH)
    if not m_path.exists():
        msg = f"単勝モデルが存在しません: {m_path}"
        logger.error(msg)
        return {
            "status": "MODEL_FILE_NOT_FOUND",
            "message": msg,
            "processed_races": 0,
            "model_inference_allowed": False,
            "candidates_found": 0,
            "notifications_sent": 0,
            "checked_at": now_dt.isoformat()
        }

    try:
        win_model = KeibaAIWinModel(model_path=m_path)
        win_model.load()
    except Exception as exc:
        msg = f"モデルロード失敗: {exc}"
        logger.error(msg)
        return {
            "status": "MODEL_LOAD_ERROR",
            "message": msg,
            "processed_races": 0,
            "model_inference_allowed": False,
            "candidates_found": 0,
            "notifications_sent": 0,
            "checked_at": now_dt.isoformat()
        }

    processed_races = 0
    all_candidates = []
    notifications_sent = 0
    errors = []

    # 安全ロック判定：設定上のフラグと正規データ認証状態の二重防護
    safety_lock_enabled = config.get("safety_lock_enabled", True)
    # 正規認可データでない場合は外部通知を強制ロック（Fail-Closed）
    if not is_certified_source:
        safety_lock_enabled = True

    for race in races:
        race_id = str(race["race_id"])
        race_time_str = race.get("race_time") or race.get("start_time")
        start_dt = datetime.fromisoformat(str(race_time_str).replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        seconds_to_start = (start_dt - now_dt).total_seconds()
        min_win = config.get("min_seconds_to_start", 300)
        max_win = config.get("max_seconds_to_start", 1800)
        in_alert_window = (min_win <= seconds_to_start <= max_win)

        horses = race.get("horses", [])

        # Only the quoted horse needs a fresh timestamp for a buy signal.
        # A scratch/no-bet price on another runner cannot suppress the race.
        fresh_win_odds = collect_fresh_win_odds(
            horses, now_dt, int(config.get("max_odds_age_seconds", 120))
        )
        if not fresh_win_odds:
            logger.info(f"レース {race_id}: 有効な単勝オッズが未取得")
            continue

        # 28特徴量の安全生成（開催日を必須渡し、架空補完なし）
        try:
            race_info = {
                "race_id": race_id,
                "race_date": race.get("race_date") or start_dt.strftime("%Y-%m-%d"),
                "race_course": race.get("race_course"),
                "surface": race.get("surface"),
                "distance": race.get("distance"),
                "condition": race.get("condition"),
                "direction": race.get("direction"),
            }
            feat_df = build_prerace_28features(race_info, horses, past_db_path=past_db_path)
        except ValueError as exc:
            err_str = f"レース {race_id} 特徴量生成エラー ({exc})"
            logger.warning(err_str)
            errors.append(err_str)
            continue

        # 単勝勝率予測
        try:
            pred_df = win_model.predict_win_race(feat_df, normalize_to_one=True)
        except Exception as exc:
            err_str = f"レース {race_id} 推論エラー ({exc})"
            logger.error(err_str)
            errors.append(err_str)
            continue

        processed_races += 1

        # 期待回収倍率の算出と候補抽出
        min_ev = config.get("min_ev", 1.15)
        min_p = config.get("min_win_prob", 0.20)

        for _, row in pred_df.iterrows():
            h_num = int(row["horse_number"])
            p_win = float(row.get("pred_win_prob", 0.0))
            h_match = next((h for h in horses if int(h["horse_number"]) == h_num), None)
            if not h_match:
                continue
            odds = fresh_win_odds.get(h_num)
            if odds is None:
                continue
            ev = round(p_win * odds, 4)
            h_name = str(row.get("horse_name", f"馬番{h_num}"))

            candidate = {
                "race_id": race_id,
                "horse_number": h_num,
                "horse_name": h_name,
                "win_odds": odds,
                "win_probability": p_win,
                "expected_value": ev,
                "seconds_to_start": seconds_to_start,
                "in_alert_window": in_alert_window,
                "eligible": (ev >= min_ev and p_win >= min_p and in_alert_window)
            }
            all_candidates.append(candidate)

            # 通知処理
            if candidate["eligible"] and not dry_run:
                event_type = f"EV_{min_ev}_WIN_{min_p}"
                
                # 重複通知防止チェック（安全ロック状態およびリトライ上限を渡す）
                max_retries = config.get("max_retries", 3)
                if is_already_delivered(
                    state_db, race_id, h_num, event_type,
                    safety_lock_active=safety_lock_enabled,
                    max_retries=max_retries
                ):
                    logger.info(f"重複通知スキップ: レース {race_id} 馬番 {h_num}")
                    continue

                # 同時実行時の二重送信防止: アトミックに送信権を獲得
                if not try_claim_alert(state_db, race_id, h_num, event_type):
                    logger.info(f"他プロセス送信中・送信済みのためスキップ: レース {race_id} 馬番 {h_num}")
                    continue

                # 安全ロックがONの場合、外部通知を遮断してスキップ記録
                if safety_lock_enabled:
                    logger.info(f"安全ロック中: 外部通知を安全に保留（レース {race_id} 馬番 {h_num}）")
                    record_alert_delivery(
                        db_path=state_db,
                        race_id=race_id,
                        horse_number=h_num,
                        horse_name=h_name,
                        event_type=event_type,
                        win_odds=odds,
                        win_probability=p_win,
                        expected_value=ev,
                        channel="none",
                        status="SKIPPED_BY_SAFETY_LOCK",
                        windows_status="BLOCKED_BY_SAFETY_LOCK",
                        discord_status="BLOCKED_BY_SAFETY_LOCK",
                        error="Safety lock active (real betting notification locked)"
                    )
                    continue

                # 外部通知の実行
                title = f"🏇 AI競馬期待値速報: レース {race_id}"
                body = f"{h_num}番 {h_name} | 勝率 {p_win*100:.1f}% | オッズ {odds:.1f}倍 | 期待値 {ev:.2f}倍 (発走まで約{int(seconds_to_start/60)}分)"

                w_status = "NOT_SENT"
                d_status = "NOT_SENT"
                err_msg = ""

                # Windows通知
                if config.get("windows_toast_enabled", True):
                    try:
                        w_ok = send_windows_notification(title, body)
                        w_status = "DELIVERED" if w_ok else "FAILED"
                        if not w_ok:
                            logger.warning(f"Windows通知送信失敗: {race_id} #{h_num}")
                    except Exception as e:
                        w_status = "FAILED"
                        err_msg += f"WindowsToastError: {e}; "

                # Discord通知
                if config.get("discord_enabled", True):
                    webhook_url = get_discord_webhook()
                    if webhook_url:
                        try:
                            import requests
                            payload = {
                                "username": "KEIBA AI Monitor",
                                "embeds": [{
                                    "title": f"🚨 【AI期待値勝負候補】レース {race_id}",
                                    "description": body,
                                    "color": 3066993,
                                    "footer": {"text": "KEIBA AI Unattended Background Monitor"}
                                }]
                            }
                            resp = requests.post(webhook_url, json=payload, timeout=8)
                            if resp.status_code in (200, 204):
                                d_status = "DELIVERED"
                            else:
                                d_status = "FAILED"
                                err_msg += f"Discord HTTP {resp.status_code}; "
                        except Exception as e:
                            d_status = "FAILED"
                            err_msg += f"DiscordError: {e}; "
                    else:
                        d_status = "NO_WEBHOOK"

                # 総合ステータス判定
                any_success = (w_status == "DELIVERED" or d_status == "DELIVERED")
                all_failed = (w_status == "FAILED" or d_status == "FAILED")
                
                if (config.get("windows_toast_enabled", True) and w_status != "DELIVERED") and \
                   (config.get("discord_enabled", True) and d_status not in ("DELIVERED", "NO_WEBHOOK")):
                    overall_status = "FAILED"
                elif any_success:
                    overall_status = "DELIVERED" if not all_failed else "PARTIAL_SUCCESS"
                elif not config.get("windows_toast_enabled", True) and not config.get("discord_enabled", True):
                    # チャネル無効設定時
                    overall_status = "DELIVERED"
                else:
                    overall_status = "DELIVERED" if not err_msg else "FAILED"

                # 履歴保存
                record_alert_delivery(
                    db_path=state_db,
                    race_id=race_id,
                    horse_number=h_num,
                    horse_name=h_name,
                    event_type=event_type,
                    win_odds=odds,
                    win_probability=p_win,
                    expected_value=ev,
                    channel="windows+discord",
                    status=overall_status,
                    windows_status=w_status,
                    discord_status=d_status,
                    error=err_msg
                )
                if overall_status in ("DELIVERED", "PARTIAL_SUCCESS"):
                    notifications_sent += 1
                logger.info(f"通知処理完了: レース {race_id} 馬番 {h_num} (status={overall_status})")

    eligible_count = sum(1 for c in all_candidates if c["eligible"])
    return {
        "status": "COMPLETED",
        "processed_races": processed_races,
        "model_inference_allowed": True,
        "candidates_found": len(all_candidates),
        "eligible_candidates": eligible_count,
        "notifications_sent": notifications_sent,
        "errors": errors,
        "safety_lock_enabled": safety_lock_enabled,
        "is_certified_source": is_certified_source,
        "checked_at": now_dt.isoformat()
    }
