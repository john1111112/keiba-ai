import sqlite3
import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict, Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "keiba.db"

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

def get_payouts(race_id: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT bet_type, combination, payout, popularity FROM payouts WHERE race_id = ?", (race_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_db_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
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

if __name__ == "__main__":
    init_db()
    print("Database initialized.")

