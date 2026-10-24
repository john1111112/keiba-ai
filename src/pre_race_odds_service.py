"""
Phase 6: 発走前オッズ収集基盤 (Pre-race Odds Service)
- 外部サイトの利用規約（無断スクレイピング禁止）を遵守
- 取得元が認める方法（公認API、公式サンプル、ローカルインポート）のみを安全に受け入れる
- 過去の値を上書きせず、追記型（Append-only）でタイムライン保存
- 発表時刻が確認できない場合は推測補完を禁止し、未確認として記録
- 発走時刻以後のデータは発走前オッズとして混入させず、厳密に遮断
"""
import datetime
import math
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
ODDS_HISTORY_DB_PATH = REPO_ROOT / "data" / "odds_history.db"


def _parse_iso_utc(value: Optional[str]) -> Optional[str]:
    """タイムゾーン付きISO-8601文字列をUTC正規化。不明・空の場合はNone（推測補完禁止）"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("none", "nan", "null", "unknown", ""):
        return None
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid ISO-8601 timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include timezone offset (e.g. +09:00)")
    return dt.astimezone(datetime.timezone.utc).isoformat(timespec="microseconds")


def validate_pre_race_record(
    record: Dict[str, Any],
    now_utc: Optional[datetime.datetime] = None
) -> Tuple[str, int, float, Optional[str], str, str, str]:
    """
    発走前オッズレコードの単体バリデーション。
    戻り値: (race_id, horse_number, odds_value, observed_at, fetched_at, source, data_status)
    """
    now = now_utc or datetime.datetime.now(datetime.timezone.utc)
    
    # 必須項目: race_id, horse_number, odds_value, fetched_at, source
    race_id = str(record.get("race_id", "")).strip()
    if not race_id or not race_id.isalnum():
        raise ValueError(f"Invalid race_id: {race_id!r}")

    try:
        h_num = int(record.get("horse_number", 0))
        if not (1 <= h_num <= 28):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError(f"Invalid horse_number: {record.get('horse_number')!r}")

    try:
        odds_val = float(record.get("odds_value", 0.0))
        if not math.isfinite(odds_val) or odds_val < 1.0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError(f"Invalid odds_value: {record.get('odds_value')!r}")

    # 取得時刻 (fetched_at): 必須
    fetched_raw = record.get("fetched_at")
    if not fetched_raw:
        raise ValueError("fetched_at is required")
    fetched_iso = _parse_iso_utc(fetched_raw)
    fetched_dt = datetime.datetime.fromisoformat(fetched_iso)

    if fetched_dt > now + datetime.timedelta(seconds=60):
        raise ValueError("fetched_at is in the future")

    # 発表時刻 (observed_at): 不明な場合は推測補完せず None
    observed_raw = record.get("observed_at")
    observed_iso = _parse_iso_utc(observed_raw)
    
    if observed_iso is not None:
        observed_dt = datetime.datetime.fromisoformat(observed_iso)
        if observed_dt > fetched_dt:
            raise ValueError("observed_at cannot be later than fetched_at")
        data_status = "pre_race_flash"
    else:
        # 発表時刻未確認（推測補完禁止）
        data_status = "pre_race_unverified_time"

    # 発走時刻 (race_start_at): 指定されている場合は発走後混入を厳格遮断
    start_raw = record.get("race_start_at") or record.get("start_at")
    if start_raw:
        start_iso = _parse_iso_utc(start_raw)
        if start_iso:
            start_dt = datetime.datetime.fromisoformat(start_iso)
            if fetched_dt >= start_dt:
                raise ValueError("Cannot ingest post-race observation as pre-race odds (fetched_at >= race_start_at)")
            if observed_iso and datetime.datetime.fromisoformat(observed_iso) >= start_dt:
                raise ValueError("Observed time is post-race (observed_at >= race_start_at)")

    source = str(record.get("source", "unknown")).strip()
    if not source:
        raise ValueError("source is required")

    return (race_id, h_num, odds_val, observed_iso, fetched_iso, source, data_status)


def ingest_pre_race_odds(
    records: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
    now_utc: Optional[datetime.datetime] = None
) -> int:
    """
    検証済み発走前オッズレコードをタイムラインDBに追記保存する。
    過去レコードの上書きは行わない（Append-only）。
    """
    if not records:
        return 0

    target_db = Path(db_path or ODDS_HISTORY_DB_PATH)
    target_db.parent.mkdir(parents=True, exist_ok=True)

    validated_rows = [validate_pre_race_record(r, now_utc=now_utc) for r in records]

    con = sqlite3.connect(target_db)
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS odds_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id TEXT NOT NULL,
            horse_id TEXT,
            horse_number INTEGER NOT NULL,
            odds_type TEXT NOT NULL,
            odds_value REAL NOT NULL,
            popularity INTEGER,
            observed_at TEXT,
            fetched_at TEXT NOT NULL,
            source TEXT NOT NULL,
            data_status TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_odds_race_type_time ON odds_timeline (race_id, odds_type, observed_at);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_odds_race_horse ON odds_timeline (race_id, horse_number);")

        inserted = 0
        for race, horse, odds, observed, fetched, source, status in validated_rows:
            # 同一レコード（同一race, horse, odds, observed/fetched, source）の二重取り込みをスキップ
            cur = con.execute("""
            INSERT INTO odds_timeline (
                race_id, horse_number, odds_type, odds_value, observed_at, fetched_at, source, data_status
            )
            SELECT ?, ?, 'win', ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM odds_timeline
                WHERE race_id = ? AND horse_number = ? AND odds_type = 'win'
                  AND odds_value = ? AND fetched_at = ? AND source = ?
            )
            """, (race, horse, odds, observed, fetched, source, status,
                  race, horse, odds, fetched, source))
            inserted += cur.rowcount

        con.commit()
    finally:
        con.close()

    return inserted
