"""
Phase 3D: 発走前時系列オッズ履歴保存基盤
- 既存DB（data/keiba.db）を一切変更せず、独立した別DB（data/odds_history.db）へ保存
- 過去の値を上書きせず、時系列スナップショット（30分前・10分前・5分前等）を追記保存
- 提供元発表時刻（observed_at）とシステム取得時刻（fetched_at）を厳密に区別
- 指定時刻（as_of_time）時点の最新オッズのみを取得し、未来オッズを完全遮断
"""
import sqlite3
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

repo_root = Path(__file__).resolve().parent.parent
ODDS_HISTORY_DB_PATH = repo_root / "data" / "odds_history.db"


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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_odds_race_type_time ON odds_timeline (race_id, odds_type, observed_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_odds_race_horse ON odds_timeline (race_id, horse_number);")
    conn.commit()
    conn.close()
    return target_path


def record_odds_snapshot(
    odds_records: List[Dict[str, Any]],
    db_path: Optional[Path] = None
) -> int:
    """
    発走前オッズのスナップショットを追記保存する（過去データの上書き禁止）。
    架空データの生成を禁止し、無効・異常値は記録しない。
    """
    if not odds_records:
        return 0

    target_path = Path(db_path or ODDS_HISTORY_DB_PATH)
    init_odds_history_db(target_path)

    conn = sqlite3.connect(target_path)
    cur = conn.cursor()

    valid_rows = []
    for r in odds_records:
        # 最低限の必須項目バリデーション
        if not r.get("race_id") or r.get("horse_number") is None:
            continue
        try:
            val = float(r.get("odds_value", 0.0))
            if val < 1.0 or not np.isfinite(val):
                continue
        except (ValueError, TypeError):
            continue

        valid_rows.append((
            str(r["race_id"]),
            str(r.get("horse_id", "")),
            int(r["horse_number"]),
            str(r.get("odds_type", "win")),
            float(val),
            int(r.get("popularity", 0)) if r.get("popularity") is not None else None,
            str(r.get("observed_at", "")),
            str(r.get("fetched_at", datetime.datetime.now().isoformat())),
            str(r.get("source", "unknown")),
            str(r.get("data_status", "pre_race_flash")),
        ))

    cur.executemany("""
    INSERT INTO odds_timeline (
        race_id, horse_id, horse_number, odds_type, odds_value,
        popularity, observed_at, fetched_at, source, data_status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, valid_rows)

    conn.commit()
    inserted_count = len(valid_rows)
    conn.close()
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
    if not target_path.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(target_path)
    query = """
    SELECT 
        t.horse_number,
        t.horse_id,
        t.odds_value,
        t.popularity,
        t.observed_at,
        t.source,
        t.data_status
    FROM odds_timeline t
    INNER JOIN (
        SELECT horse_number, MAX(observed_at) AS max_obs
        FROM odds_timeline
        WHERE race_id = ?
          AND odds_type = ?
          AND observed_at <= ?
        GROUP BY horse_number
    ) latest ON t.horse_number = latest.horse_number AND t.observed_at = latest.max_obs
    WHERE t.race_id = ?
      AND t.odds_type = ?
    ORDER BY t.horse_number ASC
    """
    df = pd.read_sql_query(query, conn, params=(race_id, odds_type, as_of_time, race_id, odds_type))
    conn.close()
    return df
