"""
バックグラウンド自動監視・AI予測・通知統合エンジン (Automated Monitor Engine)
- アプリを閉じていても、Windowsタスクまたは常駐プロセスから定期実行可能
- 認可された発走前レースをスキャンし、LightGBM 28特徴量で単勝勝率を予測
- 期待回収倍率 (EV = 勝率 × 単勝オッズ) を算出し、閾値判定
- 重複通知防止 (Idempotency) を保証し、Windowsトースト通知およびDiscord通知を送信
- 取得失敗・モデル異常・通信失敗をログに記録し、次回サイクルで自動復旧
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
from src.prerace_feature_pipeline import build_prerace_28features, validate_clean_prerace_input
from src.notifier import send_windows_notification, get_discord_webhook

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALERT_CONFIG = REPO_ROOT / "data" / "alert_config.json"
DEFAULT_STATE_DB = REPO_ROOT / "data" / "alert_state.db"
DEFAULT_LOG_PATH = REPO_ROOT / "data" / "background_monitor.log"


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
    """アラート設定（閾値、通知チャネル、安全ロック）を読み込む"""
    p = Path(config_path or DEFAULT_ALERT_CONFIG)
    default_config = {
        "min_ev": 1.15,
        "min_win_prob": 0.20,
        "max_odds_age_seconds": 120,
        "min_seconds_to_start": 300,
        "max_seconds_to_start": 1800,
        "windows_toast_enabled": True,
        "discord_enabled": True,
        "safety_lock_enabled": True,  # 本番データ検証完了まで実買い目通知をレビューモードにする
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
            error_message TEXT,
            UNIQUE(race_id, horse_number, event_type)
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_delivery_race ON alert_delivery (race_id, horse_number);")


def is_already_delivered(db_path: Path, race_id: str, horse_number: int, event_type: str) -> bool:
    """重複通知防止チェック"""
    if not db_path.exists():
        return False
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=30)) as conn:
        row = conn.execute(
            "SELECT 1 FROM alert_delivery WHERE race_id = ? AND horse_number = ? AND event_type = ?",
            (str(race_id), int(horse_number), str(event_type))
        ).fetchone()
        return row is not None


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
    error: str = ""
) -> None:
    """通知履歴をSQLiteに保存"""
    now_iso = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
        conn.execute("""
        INSERT OR REPLACE INTO alert_delivery (
            race_id, horse_number, horse_name, event_type, win_odds, win_probability,
            expected_value, first_sent_at, channel, delivery_status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(race_id), int(horse_number), str(horse_name), str(event_type),
            float(win_odds), float(win_probability), float(expected_value),
            now_iso, str(channel), str(status), str(error)
        ))


def run_monitor_scan(
    feed_path: Path,
    state_db_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    as_of_time: Optional[str] = None,
    model_path: Optional[Path] = None,
    past_db_path: Optional[Path] = None,
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

    # フィード読込
    try:
        raw_feed = json.loads(feed_file.read_text(encoding="utf-8"))
        if isinstance(raw_feed, dict) and "feed" in raw_feed:
            feed_data = raw_feed["feed"]
        else:
            feed_data = raw_feed
        races = feed_data.get("races", []) if isinstance(feed_data, dict) else []
    except Exception as exc:
        msg = f"フィード解析エラー: {exc}"
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

    for race in races:
        if not isinstance(race, dict):
            continue
        status = race.get("race_status", "upcoming")
        if status != "upcoming":
            continue

        race_id = str(race.get("race_id", ""))
        race_time_str = race.get("race_time") or race.get("start_time")
        if not race_time_str:
            continue

        try:
            start_dt = datetime.fromisoformat(str(race_time_str).replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        # 発走済みレースは遮断
        if start_dt <= now_dt:
            continue

        seconds_to_start = (start_dt - now_dt).total_seconds()
        # 発走前ウィンドウ（例: 300秒〜1800秒）のチェック
        min_win = config.get("min_seconds_to_start", 300)
        max_win = config.get("max_seconds_to_start", 1800)
        # スキャン対象として許容する範囲（ウィンドウ外でも予測自体は可能だが通知はウィンドウ内）
        in_alert_window = (min_win <= seconds_to_start <= max_win)

        horses = race.get("horses", [])
        if not horses:
            continue

        # オッズ欠損・鮮度チェック
        has_valid_odds = True
        stale_odds = False
        max_age = config.get("max_odds_age_seconds", 120)

        for h in horses:
            odds = h.get("win_odds")
            if odds is None or float(odds) < 1.0 or not math.isfinite(float(odds)):
                has_valid_odds = False
                break
            obs_raw = h.get("odds_observed_at")
            if obs_raw:
                try:
                    obs_dt = datetime.fromisoformat(str(obs_raw).replace("Z", "+00:00"))
                    if obs_dt.tzinfo is None:
                        obs_dt = obs_dt.replace(tzinfo=timezone.utc)
                    age = (now_dt - obs_dt).total_seconds()
                    if age > max_age:
                        stale_odds = True
                except Exception:
                    pass

        if not has_valid_odds:
            logger.info(f"レース {race_id}: オッズ欠損のため予測・通知を停止")
            continue

        if stale_odds:
            logger.info(f"レース {race_id}: オッズ期限切れ（Stale）のため通知を停止")
            continue

        # 28特徴量の安全生成
        try:
            race_info = {
                "race_id": race_id,
                "race_course": race.get("race_course"),
                "surface": race.get("surface"),
                "distance": race.get("distance"),
                "condition": race.get("condition", "良"),
                "direction": race.get("direction", "右"),
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
            odds = float(h_match["win_odds"])
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
                if is_already_delivered(state_db, race_id, h_num, event_type):
                    logger.info(f"重複通知スキップ: レース {race_id} 馬番 {h_num}")
                    continue

                # 通知送信
                title = f"🏇 AI競馬期待値速報: レース {race_id}"
                body = f"{h_num}番 {h_name} | 勝率 {p_win*100:.1f}% | オッズ {odds:.1f}倍 | 期待値 {ev:.2f}倍 (発走まで約{int(seconds_to_start/60)}分)"

                sent_ok = True
                err_msg = ""

                # Windows通知
                if config.get("windows_toast_enabled", True):
                    try:
                        w_ok = send_windows_notification(title, body)
                        if not w_ok:
                            logger.warning(f"Windows通知送信失敗: {race_id} #{h_num}")
                    except Exception as e:
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
                            if resp.status_code not in (200, 204):
                                err_msg += f"Discord HTTP {resp.status_code}; "
                        except Exception as e:
                            err_msg += f"DiscordError: {e}; "

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
                    status="DELIVERED" if not err_msg else "PARTIAL_ERROR",
                    error=err_msg
                )
                notifications_sent += 1
                logger.info(f"通知完了: レース {race_id} 馬番 {h_num} (EV={ev})")

    eligible_count = sum(1 for c in all_candidates if c["eligible"])
    return {
        "status": "COMPLETED",
        "processed_races": processed_races,
        "model_inference_allowed": True,
        "candidates_found": len(all_candidates),
        "eligible_candidates": eligible_count,
        "notifications_sent": notifications_sent,
        "errors": errors,
        "checked_at": now_dt.isoformat()
    }
