"""
Phase 3D: 発走前時系列オッズ履歴保存基盤
- 既存DB（data/keiba.db）を一切変更せず、独立した別DB（data/odds_history.db）へ保存
- 過去の値を上書きせず、時系列スナップショット（30分前・10分前・5分前等）を追記保存
- 提供元発表時刻（observed_at）とシステム取得時刻（fetched_at）を厳密に区別
- 指定時刻（as_of_time）時点の最新オッズのみを取得し、未来オッズを完全遮断
- 発走前オッズおよびAI予測スナップショットの時系列管理（実時刻付き）
"""
import sqlite3
import math
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

repo_root = Path(__file__).resolve().parent.parent
ODDS_HISTORY_DB_PATH = repo_root / "data" / "odds_history.db"

PROHIBITED_STATUSES = {"final_settled", "final", "post_race", "settled", "result"}


def init_odds_history_db(db_path: Optional[Path] = None) -> Path:
    """発走前オッズ履歴専用の別SQLiteデータベースを初期化する（既存DB変更ゼロ）"""
    target_path = Path(db_path or ODDS_HISTORY_DB_PATH)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(target_path)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS odds_timeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        race_id TEXT NOT NULL,
        horse_id TEXT,
        horse_number INTEGER NOT NULL,
        odds_type TEXT NOT NULL,
        odds_value REAL NOT NULL,
        popularity INTEGER,
        observed_at TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        source TEXT NOT NULL,
        data_status TEXT NOT NULL,
        timing_tag TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_odds_race_type_time ON odds_timeline (race_id, odds_type, observed_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_odds_race_horse ON odds_timeline (race_id, horse_number);")

    # 既存テーブルに timing_tag カラムが存在しない場合のマイグレーション
    cols = {row[1] for row in cur.execute("PRAGMA table_info(odds_timeline)").fetchall()}
    if "timing_tag" not in cols:
        cur.execute("ALTER TABLE odds_timeline ADD COLUMN timing_tag TEXT;")

    # AI予測スナップショット記録テーブル
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_predictions_timeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        race_id TEXT NOT NULL,
        horse_number INTEGER NOT NULL,
        horse_name TEXT,
        pred_place_score REAL,
        pred_win_prob REAL,
        market_odds REAL,
        market_prob REAL,
        blended_win_prob REAL,
        expected_value REAL,
        rank INTEGER,
        timing_tag TEXT NOT NULL,
        predicted_at TEXT NOT NULL,
        odds_observed_at TEXT,
        odds_fetched_at TEXT,
        odds_status TEXT NOT NULL,
        model_version TEXT NOT NULL,
        model_sha256 TEXT,
        status TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pred_race_timing ON ai_predictions_timeline (race_id, timing_tag);")

    # スナップショット実行監査テーブル
    cur.execute("""
    CREATE TABLE IF NOT EXISTS asof_snapshot_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        race_id TEXT NOT NULL,
        timing_tag TEXT NOT NULL,
        scheduled_race_time TEXT NOT NULL,
        target_snapshot_time TEXT NOT NULL,
        executed_at TEXT NOT NULL,
        odds_status TEXT NOT NULL,
        odds_count INTEGER NOT NULL DEFAULT 0,
        pred_status TEXT NOT NULL,
        pred_count INTEGER NOT NULL DEFAULT 0,
        error_message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_race_timing ON asof_snapshot_runs (race_id, timing_tag);")

    conn.commit()
    conn.close()
    return target_path


def record_odds_snapshot(
    odds_records: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
    timing_tag: Optional[str] = None
) -> int:
    """
    発走前オッズのスナップショットを追記保存する（過去データの上書き禁止）。
    架空データの生成を禁止し、確定オッズ・無効・異常値は記録しない。
    """
    if not odds_records:
        return 0

    target_path = Path(db_path or ODDS_HISTORY_DB_PATH)
    valid_rows = []
    for r in odds_records:
        # 最低限の必須項目バリデーション
        if not r.get("race_id") or r.get("horse_number") is None:
            raise ValueError("Missing race ID or horse number")
        try:
            val = float(r.get("odds_value", 0.0))
            if val < 1.0 or not np.isfinite(val):
                raise ValueError("Invalid odds value")
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid odds value") from exc

        observed_at = r.get("observed_at")
        fetched_at = r.get("fetched_at")  # Never invent a fetch timestamp
        try:
            observed_dt = datetime.datetime.fromisoformat(str(observed_at))
            fetched_dt = datetime.datetime.fromisoformat(str(fetched_at))
            if observed_dt.tzinfo is None or fetched_dt.tzinfo is None or observed_dt.utcoffset() is None or fetched_dt.utcoffset() is None:
                raise ValueError("Timezone required for both timestamps")
            if observed_dt > fetched_dt:
                raise ValueError("Observed timestamp after fetched timestamp")
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid odds timestamps") from exc

        status_str = str(r.get("data_status", "")).strip().lower()
        if status_str in {"", "unknown", "none"}:
            raise ValueError("Odds data status must be explicitly declared")
        if status_str in PROHIBITED_STATUSES:
            raise ValueError("Final or post-race odds prohibited")
        if status_str not in {"pre_race_flash", "manual_prerace"}:
            raise ValueError("Unrecognized odds status")

        if str(r.get("source", "unknown")).strip().lower() in {"", "unknown", "none"}:
            raise ValueError("Unknown odds source")
        if str(r.get("odds_type", "win")) != "win":
            raise ValueError("Only win odds supported")
        try:
            raw_horse = r["horse_number"]
            if isinstance(raw_horse, bool) or str(raw_horse).strip() != str(int(raw_horse)):
                raise ValueError("Horse number must be an integer")
            horse_num = int(raw_horse)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid horse number") from exc
        if horse_num < 1:
            raise ValueError("Invalid horse number")
        row_timing = r.get("timing_tag") or timing_tag or ""
        if row_timing and row_timing not in {"pre_30m", "pre_10m", "pre_5m", "manual"}:
            raise ValueError("Invalid timing tag")

        valid_rows.append((
            str(r["race_id"]),
            str(r.get("horse_id", "")),
            horse_num,
            "win",
            float(val),
            int(r.get("popularity", 0)) if r.get("popularity") is not None else None,
            str(observed_at),
            str(fetched_at),
            str(r.get("source", "unknown")),
            str(r["data_status"]),
            str(row_timing),
        ))

    if not valid_rows:
        return 0
    if len({(row[0], row[2], row[7]) for row in valid_rows}) != len(valid_rows):
        raise ValueError("Duplicate horse at same fetched time")
    # Reject contradictory replays instead of silently appending a different
    # price for the same horse and exact fetch timestamp.
    init_odds_history_db(target_path)
    # BEGIN IMMEDIATE serializes competing writers. The conflict check and
    # insertion share the same transaction, so two recorder processes cannot
    # both pass the check before either inserts.
    with sqlite3.connect(target_path, timeout=30) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for row in valid_rows:
            if conn.execute(
                "SELECT 1 FROM odds_timeline "
                "WHERE race_id=? AND horse_number=? AND fetched_at=? LIMIT 1",
                (row[0], row[2], row[7])
            ).fetchone():
                raise ValueError("Duplicate existing odds timestamp; immutable append-only history")
        conn.executemany("""
    INSERT INTO odds_timeline (
        race_id, horse_id, horse_number, odds_type, odds_value,
        popularity, observed_at, fetched_at, source, data_status, timing_tag
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, valid_rows)
    inserted_count = len(valid_rows)
    return inserted_count


def get_latest_odds_as_of(
    race_id: str,
    as_of_time: str,
    odds_type: str = "win",
    db_path: Optional[Path] = None
) -> pd.DataFrame:
    """
    【予測時点の再現】
    指定した時刻（as_of_time）までに発表・観測された最新のオッズのみを取得する。
    未来のオッズ（as_of_time より後の時刻）および確定オッズは厳密に遮断され参照不可。
    """
    target_path = Path(db_path or ODDS_HISTORY_DB_PATH)
    cutoff = _parse_aware_utc(as_of_time)
    if cutoff is None:
        raise ValueError("as_of_time must have an explicit timezone")
    if not target_path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(f"file:{target_path.resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute("""
            SELECT horse_number, horse_id, odds_value, popularity, observed_at,
                   fetched_at, source, data_status, timing_tag, id
            FROM odds_timeline
            WHERE race_id = ? AND odds_type = ?
        """, (race_id, odds_type)).fetchall()
    finally:
        conn.close()
    latest = {}
    for horse, horse_id, odds, popularity, observed, fetched, source, status, tag, row_id in rows:
        obs, got = _parse_aware_utc(observed), _parse_aware_utc(fetched)
        if (obs is None or got is None or obs > got or obs > cutoff or got > cutoff
                or str(status).strip().lower() not in {"pre_race_flash", "manual_prerace"}
                or str(source).strip().lower() in {"", "unknown", "none"}):
            continue
        try:
            price = float(odds)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price < 1:
            continue
        # Stable tie-breaker: publication time, fetch time, insertion id.
        key = (obs, got, row_id)
        if horse not in latest or key > latest[horse][0]:
            latest[horse] = (key, {
                "horse_number": horse, "horse_id": horse_id, "odds_value": price,
                "popularity": popularity, "observed_at": observed,
                "fetched_at": fetched, "source": source, "data_status": status,
                "timing_tag": tag
            })
    return pd.DataFrame([entry[1] for _, entry in sorted(latest.items())])


def _parse_aware_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(datetime.timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None
