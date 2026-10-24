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


def _canonical_time(value: str) -> str:
    """Normalize timestamps to UTC, preserving naive legacy timestamps as UTC.

    Producers should supply offset-aware ISO-8601 timestamps; naive values are
    accepted for compatibility with existing Phase 3D test fixtures.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Timestamp must be a nonempty ISO-8601 string")
    try:
        dt = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid ISO-8601 timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).isoformat(timespec="microseconds")


def record_odds_snapshot(
    odds_records: List[Dict[str, Any]],
    db_path: Optional[Path] = None
) -> int:
    """Append only valid odds observations, never replacing past snapshots.

    Both timestamps are mandatory. A caller may not backdate fetched_at: this
    method validates format but cannot authenticate a data provider's clock.
    """
    if not odds_records:
        return 0
    target_path = Path(db_path or ODDS_HISTORY_DB_PATH)
    valid_rows = []
    for r in odds_records:
        if not r.get("race_id") or r.get("horse_number") is None:
            continue
        try:
            number = int(r["horse_number"])
            odds = float(r["odds_value"])
            observed = _canonical_time(r["observed_at"])
            fetched = _canonical_time(r["fetched_at"])
            if number <= 0 or odds < 1.0 or not np.isfinite(odds):
                continue
            # A future-dated observation cannot be treated as available yet.
            if observed > fetched:
                continue
            status = str(r.get("data_status", "pre_race_flash"))
            if not status or not str(r.get("source", "")).strip():
                continue
            valid_rows.append((
                str(r["race_id"]), str(r.get("horse_id") or ""), number,
                str(r.get("odds_type", "win")), odds,
                int(r["popularity"]) if r.get("popularity") is not None else None,
                observed, fetched, str(r["source"]), status,
            ))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    if not valid_rows:
        return 0
    init_odds_history_db(target_path)
    with sqlite3.connect(target_path) as conn:
        conn.executemany("""
        INSERT INTO odds_timeline (
            race_id, horse_id, horse_number, odds_type, odds_value,
            popularity, observed_at, fetched_at, source, data_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, valid_rows)
    return len(valid_rows)


def get_latest_odds_as_of(
    race_id: str,
    as_of_time: str,
    odds_type: str = "win",
    db_path: Optional[Path] = None,
    include_sample: bool = False
) -> pd.DataFrame:
    """Get the last *available* pre-race quote for each horse.

    Availability requires BOTH publication and receipt by as_of_time.
    Only explicitly pre-race observations are eligible; sample rows are excluded
    by default and can only be included with include_sample=True.
    When timestamps tie, the most recently inserted eligible row wins.
    """
    target_path = Path(db_path or ODDS_HISTORY_DB_PATH)
    if not target_path.exists():
        return pd.DataFrame()
    cutoff = _canonical_time(as_of_time)
    with sqlite3.connect(target_path) as conn:
        # SQLite julianday() accepts legacy naive ISO strings as well as UTC
        # offset timestamps; avoids string-order bugs with mixed offsets.
        query = """
        SELECT horse_number, horse_id, odds_value, popularity,
               observed_at, fetched_at, source, data_status
        FROM (
            SELECT t.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY horse_number
                       ORDER BY julianday(observed_at) DESC,
                                julianday(fetched_at) DESC, id DESC
                   ) AS row_rank
            FROM odds_timeline t
            WHERE race_id = ? AND odds_type = ?
              AND julianday(observed_at) <= julianday(?)
              AND julianday(fetched_at) <= julianday(?)
              AND (data_status IN ('pre_race_flash', 'pre_race') OR data_status LIKE 'pre_%')
              AND (? = 1 OR source <> 'sample')
        ) ranked
        WHERE row_rank = 1
        ORDER BY horse_number ASC
        """
        return pd.read_sql_query(query, conn, params=(race_id, odds_type, cutoff, cutoff, int(include_sample)))
