import sqlite3
import pandas as pd
from pathlib import Path
from typing import Dict, Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "keiba.db"

def init_tracker_db():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ai_tracking_history (
        race_id TEXT PRIMARY KEY,
        race_name TEXT,
        race_date TEXT,
        race_course TEXT,
        honmei_horse_num INTEGER,
        honmei_horse_name TEXT,
        honmei_pred_score REAL,
        honmei_odds REAL,
        finish_order INTEGER,
        is_win INTEGER,
        is_place INTEGER,
        payout_win INTEGER,
        payout_place INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def update_tracking_history(pred_df: pd.DataFrame):
    """
    全レースのAI本命予想と結果を突き合わせ、トラッキングテーブルに全件同期
    """
    init_tracker_db()
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    
    # レースごとに本命馬(◎)の予想と実際の結果を抽出
    races = pred_df.groupby("race_id")
    records = []
    
    for rid, group in races:
        sorted_g = group.sort_values("pred_score", ascending=False).reset_index(drop=True)
        if len(sorted_g) == 0:
            continue
        top = sorted_g.iloc[0]
        
        is_win = 1 if top["finish_order"] == 1 else 0
        is_place = 1 if top["finish_order"] <= 3 else 0
        payout_win = int(top["odds"] * 100) if is_win else 0
        p_odds = max(1.1, round(top["odds"] * 0.35, 1))
        payout_place = int(p_odds * 100) if is_place else 0
        
        records.append((
            str(top["race_id"]),
            str(top.get("race_name", "レース")),
            str(top.get("date", "2026-10-25")),
            str(top.get("race_course", "東京")),
            int(top["horse_number"]),
            str(top["horse_name"]),
            float(top["pred_score"]),
            float(top["odds"]),
            int(top["finish_order"]),
            is_win,
            is_place,
            payout_win,
            payout_place
        ))
        
    cur.executemany("""
    INSERT OR REPLACE INTO ai_tracking_history (
        race_id, race_name, race_date, race_course,
        honmei_horse_num, honmei_horse_name, honmei_pred_score,
        honmei_odds, finish_order, is_win, is_place, payout_win, payout_place
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    
    conn.commit()
    conn.close()
    print(f"Tracking history updated: {len(records)} races tracked.")

if __name__ == "__main__":
    from pathlib import Path
    import pandas as pd
    from src.model import KeibaAIModel
    
    p = Path("data/features.parquet")
    if p.exists():
        df = pd.read_parquet(p)
        model = KeibaAIModel()
        pred = model.predict_race(df)
        update_tracking_history(pred)
