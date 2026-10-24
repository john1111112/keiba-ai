import sqlite3
import pandas as pd
from pathlib import Path

root = Path(__file__).resolve().parent.parent
db_path = root / "data" / "keiba.db"
feat_path = root / "data" / "features.parquet"
cache_path = root / "data" / "race_rank_cache.parquet"

conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
cur.execute("DELETE FROM race_results WHERE race_id LIKE '2027%' OR horse_id LIKE 'h_%'")
cur.execute("DELETE FROM races WHERE race_id LIKE '2027%' OR race_id NOT IN (SELECT DISTINCT race_id FROM race_results)")
conn.commit()
cur.execute("VACUUM")
conn.close()

if feat_path.exists():
    df_f = pd.read_parquet(feat_path)
    df_f = df_f[~df_f["race_id"].astype(str).str.startswith("2027") & ~df_f["horse_id"].astype(str).str.startswith("h_")].copy()
    df_f.to_parquet(feat_path, index=False)
    print(f"Cleaned features.parquet: {len(df_f)} real JRA rows remaining.")

if cache_path.exists():
    df_c = pd.read_parquet(cache_path)
    df_c = df_c[~df_c["race_id"].astype(str).str.startswith("2027")].copy()
    df_c.to_parquet(cache_path, index=False)
    print(f"Cleaned race_rank_cache.parquet: {len(df_c)} real JRA races remaining.")
