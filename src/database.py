import sqlite3
import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "keiba.db"
INCOMPLETE_RACES_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "incomplete_races_report.csv"


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS races (
        race_id TEXT PRIMARY KEY,
        race_name TEXT,
        date TEXT,
        race_course TEXT,
        race_number INTEGER,
        surface TEXT,
        distance INTEGER,
        direction TEXT,
        weather TEXT,
        condition TEXT,
        grade TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS race_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        race_id TEXT,
        finish_order INTEGER,
        bracket_number INTEGER,
        horse_number INTEGER,
        horse_id TEXT,
        horse_name TEXT,
        sex TEXT,
        age INTEGER,
        weight_carry REAL,
        jockey_id TEXT,
        jockey_name TEXT,
        time_str TEXT,
        time_sec REAL,
        margin_str TEXT,
        last_3f REAL,
        odds REAL,
        popularity INTEGER,
        horse_weight REAL,
        horse_weight_diff REAL,
        trainer_id TEXT,
        trainer_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (race_id) REFERENCES races (race_id),
        UNIQUE (race_id, horse_number)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        race_id TEXT,
        bet_type TEXT,
        combination TEXT,
        payout INTEGER,
        popularity INTEGER,
        FOREIGN KEY (race_id) REFERENCES races (race_id)
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_races_date ON races (date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_results_horse ON race_results (horse_id, race_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_results_jockey ON race_results (jockey_id)")

    conn.commit()
    conn.close()


def save_race(race_info: Dict[str, Any], results: List[Dict[str, Any]], payouts: Optional[List[Dict[str, Any]]] = None, db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        cur.execute("""
        INSERT OR REPLACE INTO races (
            race_id, race_name, date, race_course, race_number,
            surface, distance, direction, weather, condition, grade
        ) VALUES (
            :race_id, :race_name, :date, :race_course, :race_number,
            :surface, :distance, :direction, :weather, :condition, :grade
        )
        """, race_info)

        for res in results:
            res["race_id"] = race_info["race_id"]
            cur.execute("""
            INSERT OR REPLACE INTO race_results (
                race_id, finish_order, bracket_number, horse_number,
                horse_id, horse_name, sex, age, weight_carry,
                jockey_id, jockey_name, time_str, time_sec, margin_str,
                last_3f, odds, popularity, horse_weight, horse_weight_diff,
                trainer_id, trainer_name
            ) VALUES (
                :race_id, :finish_order, :bracket_number, :horse_number,
                :horse_id, :horse_name, :sex, :age, :weight_carry,
                :jockey_id, :jockey_name, :time_str, :time_sec, :margin_str,
                :last_3f, :odds, :popularity, :horse_weight, :horse_weight_diff,
                :trainer_id, :trainer_name
            )
            """, res)

        if payouts:
            cur.execute("DELETE FROM payouts WHERE race_id = ?", (race_info["race_id"],))
            for p in payouts:
                p["race_id"] = race_info["race_id"]
                cur.execute("""
                INSERT INTO payouts (race_id, bet_type, combination, payout, popularity)
                VALUES (:race_id, :bet_type, :combination, :payout, :popularity)
                """, p)

        conn.commit()
    finally:
        conn.close()


def load_all_race_data(db_path: Optional[Path] = None) -> pd.DataFrame:
    conn = get_connection(db_path)
    query = """
    SELECT 
        r.race_id, r.race_name, r.date, r.race_course, r.race_number,
        r.surface, r.distance, r.direction, r.weather, r.condition, r.grade,
        res.finish_order, res.bracket_number, res.horse_number,
        res.horse_id, res.horse_name, res.sex, res.age, res.weight_carry,
        res.jockey_id, res.jockey_name, res.time_str, res.time_sec,
        res.margin_str, res.last_3f, res.odds, res.popularity,
        res.horse_weight, res.horse_weight_diff, res.trainer_id, res.trainer_name,
        res.sire, res.sire_type, res.running_style, res.corner_4_pos, res.rest_weeks
    FROM races r
    JOIN race_results res ON r.race_id = res.race_id
    ORDER BY r.date ASC, r.race_id ASC, res.horse_number ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def evaluate_race_completeness_and_export_csv(
    df_all: Optional[pd.DataFrame] = None,
    db_path: Optional[Path] = None,
    csv_out_path: Optional[Path] = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    【Phase 3A: 不完全レース判定ロジックとCSV出力】
    - 1〜3着馬の存在を確認する。
    - 出走頭数（entry_count）5頭以上を基本条件とする（JRA施行規程上の最小出走頭数は5頭）。
    - 最大馬番と収録頭数が一致しないだけで不完全と断定しない（出走取消や除外による馬番欠番を考慮する）。
    - スクレイピング時に取消・除外・競走中止（time_str が空文字または '-' で finish_order=15 等が入っている行）を非完走馬として識別し、
      完走馬（n_valid_finishers）の中で最大着順 max_valid_fo <= n_valid_finishers（1〜n_valid_finishers着が連続しているか）を照合する。
    - 不完全と判定したレースID・理由・過去走履歴としての利用可否をCSVに出力する。
    """
    if df_all is None:
        df_all = load_all_race_data(db_path)

    out_csv = csv_out_path or INCOMPLETE_RACES_CSV_PATH
    summary_records = []
    incomplete_records = []

    for rid, grp in df_all.groupby("race_id", sort=False):
        r0 = grp.iloc[0]
        d_str = str(r0["date"])[:10]
        course = str(r0["race_course"])
        r_num = int(r0["race_number"])
        r_name = str(r0["race_name"])
        entry_count = len(grp)
        max_hn = int(grp["horse_number"].max()) if entry_count > 0 else 0

        fo_series = pd.to_numeric(grp["finish_order"], errors="coerce").fillna(0).astype(int)
        t_series = grp["time_str"].fillna("").astype(str).str.strip()

        # 未確定レース判定: 全馬が finish_order == 0（日付だけで確定扱いしない）
        is_unconfirmed = bool((fo_series == 0).all())
        if is_unconfirmed:
            is_valid_entry = (entry_count >= 5)
            status_str = "UPCOMING_UNCONFIRMED"
            reason = "" if is_valid_entry else f"UPCOMING_UNDER_5_ENTRIES(entry_count={entry_count})"
            summary_records.append({
                "race_id": str(rid),
                "date": d_str,
                "status": status_str,
                "is_confirmed": False,
                "is_confirmed_complete": False,  # 未確定のため学習・バックテスト評価の対象外
                "is_unconfirmed": True,
                "is_incomplete": (not is_valid_entry),
                "is_complete_for_eval": False,   # 過去データ学習・評価用フラグは厳密にFalse
                "is_complete_card": is_valid_entry,
                "usable_for_training_eval": False,
                "usable_for_horse_history": False,
                "entry_count": entry_count,
                "valid_finishers": 0,
                "scratched_or_aborted": 0,
                "incomplete_reason": reason,
            })
            if not is_valid_entry:
                incomplete_records.append({
                    "race_id": str(rid),
                    "date": d_str,
                    "race_course": course,
                    "race_number": r_num,
                    "race_name": r_name,
                    "entry_count": entry_count,
                    "max_horse_number": max_hn,
                    "valid_finishers": 0,
                    "scratched_or_aborted": 0,
                    "max_valid_finish_order": 0,
                    "reason": reason,
                    "usable_for_training_eval": False,
                    "usable_for_horse_history": False,
                })
            continue

        # 確定結果が存在するレース:
        # 完走確定馬 = finish_order が 1..28 かつ 走破時計(time_str)が記録されている馬
        # 取消・除外・競走中止馬 = time_str が '' または '-' または finish_order > 28
        valid_fin_mask = (fo_series >= 1) & (fo_series <= 28) & (~t_series.isin(["", "-"]))
        scratched_or_aborted_count = int((~valid_fin_mask).sum())
        valid_fos = set(fo_series[valid_fin_mask].tolist())
        n_valid = int(valid_fin_mask.sum())
        max_valid_fo = max(valid_fos) if valid_fos else 0

        reasons = []
        if entry_count < 5:
            reasons.append(f"UNDER_5_ENTRIES(entry_count={entry_count})")
        if not {1, 2, 3}.issubset(valid_fos):
            missing_top3 = sorted(list({1, 2, 3} - valid_fos))
            reasons.append(f"MISSING_TOP3_FINISHERS(missing={missing_top3})")
        if max_valid_fo > n_valid:
            missing_ranks = sorted(list(set(range(1, max_valid_fo + 1)) - valid_fos))
            reasons.append(
                f"PARTIAL_CARD_MISSING_FINISH_ORDERS(valid_finishers={n_valid},max_finish_order={max_valid_fo},missing_ranks={len(missing_ranks)})"
            )

        is_complete = (len(reasons) == 0)
        reason_str = "; ".join(reasons)
        # 過去走抜粋によってレース全体としては不完全でも、個別の完走馬レコード（valid_fin_mask == True）はその馬自身の過去走履歴としては実在データのため利用可能
        usable_hist = bool(n_valid > 0)

        status_str = "PAST_CONFIRMED_COMPLETE" if is_complete else "PAST_INCOMPLETE_EXTRACT"
        summary_records.append({
            "race_id": str(rid),
            "date": d_str,
            "status": status_str,
            "is_confirmed": True,
            "is_confirmed_complete": is_complete,
            "is_unconfirmed": False,
            "is_incomplete": (not is_complete),
            "is_complete_for_eval": is_complete,
            "usable_for_training_eval": is_complete,
            "usable_for_horse_history": usable_hist,
            "entry_count": entry_count,
            "valid_finishers": n_valid,
            "scratched_or_aborted": scratched_or_aborted_count,
            "incomplete_reason": reason_str,
        })

        if not is_complete:
            incomplete_records.append({
                "race_id": str(rid),
                "date": d_str,
                "race_course": course,
                "race_number": r_num,
                "race_name": r_name,
                "entry_count": entry_count,
                "max_horse_number": max_hn,
                "valid_finishers": n_valid,
                "scratched_or_aborted": scratched_or_aborted_count,
                "max_valid_finish_order": max_valid_fo,
                "reason": reason_str,
                "usable_for_training_eval": False,
                "usable_for_horse_history": usable_hist,
            })

    summary_df = pd.DataFrame(summary_records)
    incomplete_df = pd.DataFrame(incomplete_records)
    if len(incomplete_df) > 0:
        incomplete_df = incomplete_df.sort_values(["date", "race_id"]).reset_index(drop=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    incomplete_df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    return summary_df, incomplete_df


def get_races_metadata(db_path: Optional[Path] = None, recent_days_window: int = 30) -> pd.DataFrame:
    """
    【固定日付撤去版】
    DB内の最新開催日を基準に直近 `recent_days_window` 日間のレースメタデータと、
    確定状態（is_confirmed: 日付ではなく finish_order >= 1 の有無で判定）・完全性フラグを動的に取得する（SELECT専用）。
    """
    conn = get_connection(db_path)
    cur = conn.cursor()
    max_date_row = cur.execute("SELECT MAX(date) FROM races").fetchone()
    max_date_str = str(max_date_row[0])[:10] if (max_date_row and max_date_row[0]) else "2099-12-31"
    min_window_dt = (pd.to_datetime(max_date_str) - pd.Timedelta(days=recent_days_window)).strftime("%Y-%m-%d")

    query = """
    SELECT
        r.race_id, r.race_name, r.date, r.race_course, r.race_number,
        r.surface, r.distance, r.condition,
        COUNT(res.horse_number) AS entry_count,
        CASE
            WHEN SUM(CASE WHEN res.finish_order >= 1 AND res.time_str NOT IN ('', '-') THEN 1 ELSE 0 END) > 0 THEN 1
            ELSE 0
        END AS is_confirmed,
        CASE
            WHEN COUNT(res.horse_number) >= 5 AND (
                SUM(CASE WHEN res.finish_order = 0 THEN 1 ELSE 0 END) = COUNT(res.horse_number)
                OR (
                    SUM(CASE WHEN res.finish_order = 1 AND res.time_str NOT IN ('', '-') THEN 1 ELSE 0 END) >= 1
                    AND SUM(CASE WHEN res.finish_order = 2 AND res.time_str NOT IN ('', '-') THEN 1 ELSE 0 END) >= 1
                    AND SUM(CASE WHEN res.finish_order = 3 AND res.time_str NOT IN ('', '-') THEN 1 ELSE 0 END) >= 1
                    AND MAX(CASE WHEN res.finish_order BETWEEN 1 AND 28 AND res.time_str NOT IN ('', '-') THEN res.finish_order ELSE 0 END)
                        <= SUM(CASE WHEN res.finish_order BETWEEN 1 AND 28 AND res.time_str NOT IN ('', '-') THEN 1 ELSE 0 END)
                )
            ) THEN 1
            ELSE 0
        END AS is_complete_card
    FROM races r
    LEFT JOIN race_results res ON r.race_id = res.race_id
    WHERE r.date >= ?
    GROUP BY r.race_id
    ORDER BY r.date DESC, r.race_course ASC, r.race_number ASC
    """
    df = pd.read_sql_query(query, conn, params=(min_window_dt,))
    conn.close()
    return df


def get_payouts(race_id: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT bet_type, combination, payout, popularity FROM payouts WHERE race_id = ?", (race_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_db_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    データベース統計取得関数（SELECT専用・副作用ゼロ）。
    """
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM races")
    race_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM race_results")
    result_count = cur.fetchone()[0]
    cur.execute("SELECT MIN(date), MAX(date) FROM races")
    dates = cur.fetchone()
    conn.close()
    return {
        "race_count": race_count,
        "result_count": result_count,
        "min_date": dates[0] if dates else None,
        "max_date": dates[1] if dates else None
    }


def preview_cleanup_targets(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    削除候補レコードの件数を事前確認する管理用関数（SELECT専用・固定日付なし・実削除は一切行わない）。
    """
    conn = get_connection(db_path)
    cur = conn.cursor()
    synthetic_races = cur.execute(
        "SELECT COUNT(*) FROM races WHERE LENGTH(race_id) != 12 OR SUBSTR(race_id, 5, 2) NOT IN ('01','02','03','04','05','06','07','08','09','10')"
    ).fetchone()[0]
    synthetic_results = cur.execute("SELECT COUNT(*) FROM race_results WHERE horse_id LIKE 'h_%'").fetchone()[0]
    orphan_payouts = cur.execute("SELECT COUNT(*) FROM payouts WHERE race_id NOT IN (SELECT race_id FROM races)").fetchone()[0]
    partial_races = cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT r.race_id
            FROM races r
            LEFT JOIN race_results res ON r.race_id = res.race_id
            GROUP BY r.race_id
            HAVING SUM(CASE WHEN res.finish_order = 0 THEN 1 ELSE 0 END) = 0
               AND (
                   COUNT(res.horse_number) < 5
                   OR SUM(CASE WHEN res.finish_order = 1 AND res.time_str NOT IN ('', '-') THEN 1 ELSE 0 END) = 0
                   OR SUM(CASE WHEN res.finish_order = 2 AND res.time_str NOT IN ('', '-') THEN 1 ELSE 0 END) = 0
                   OR SUM(CASE WHEN res.finish_order = 3 AND res.time_str NOT IN ('', '-') THEN 1 ELSE 0 END) = 0
                   OR MAX(CASE WHEN res.finish_order BETWEEN 1 AND 28 AND res.time_str NOT IN ('', '-') THEN res.finish_order ELSE 0 END)
                      > SUM(CASE WHEN res.finish_order BETWEEN 1 AND 28 AND res.time_str NOT IN ('', '-') THEN 1 ELSE 0 END)
               )
        )
    """).fetchone()[0]
    conn.close()
    return {
        "synthetic_races": synthetic_races,
        "synthetic_results": synthetic_results,
        "orphan_payouts": orphan_payouts,
        "partial_past_races": partial_races,
    }


def execute_confirmed_cleanup(
    db_path: Optional[Path] = None,
    delete_orphan_payouts: bool = False,
    delete_synthetic: bool = False,
    confirm: bool = False
) -> Dict[str, Any]:
    """
    事前確認後に明示的な confirm=True が指定された場合のみ削除を実行する管理専用関数。
    """
    preview = preview_cleanup_targets(db_path)
    if not confirm:
        return {"executed": False, "preview": preview, "message": "confirm=False のため削除は実行されませんでした（ドライラン）。"}

    conn = get_connection(db_path)
    cur = conn.cursor()
    deleted_counts = {"synthetic_results": 0, "synthetic_races": 0, "orphan_payouts": 0}
    try:
        if delete_synthetic:
            cur.execute(
                "DELETE FROM race_results WHERE horse_id LIKE 'h_%' OR LENGTH(race_id) != 12 OR SUBSTR(race_id, 5, 2) NOT IN ('01','02','03','04','05','06','07','08','09','10')"
            )
            deleted_counts["synthetic_results"] = cur.rowcount
            cur.execute(
                "DELETE FROM races WHERE LENGTH(race_id) != 12 OR SUBSTR(race_id, 5, 2) NOT IN ('01','02','03','04','05','06','07','08','09','10')"
            )
            deleted_counts["synthetic_races"] = cur.rowcount
        if delete_orphan_payouts:
            cur.execute("DELETE FROM payouts WHERE race_id NOT IN (SELECT race_id FROM races)")
            deleted_counts["orphan_payouts"] = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"executed": True, "deleted": deleted_counts, "preview_before": preview}


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
