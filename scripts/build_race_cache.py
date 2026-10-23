import sys
import io
import time
import pandas as pd
import numpy as np
from pathlib import Path

# Ensure UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.database import load_all_race_data
from src.features import build_features
from src.model import KeibaAIModel

def main():
    t0 = time.time()
    feat_path = Path("data/features.parquet")
    if feat_path.exists():
        print(f"Loading cached {feat_path}...")
        df_feat = pd.read_parquet(feat_path)
    else:
        print("Loading raw race data from SQLite...")
        df_raw = load_all_race_data()
        df_clean = df_raw[df_raw["date"] <= "2026-10-25"].copy()
        print(f"Loaded {len(df_clean)} rows, {df_clean['race_id'].nunique()} races.")
        print("Building features...")
        df_feat, fcols = build_features(df_clean)
        df_feat.to_parquet(feat_path, index=False)
        print(f"Saved {feat_path} ({len(df_feat)} rows).")

    print("Predicting with model...")
    model = KeibaAIModel()
    model.load()
    X = df_feat[model.feature_cols]
    df_feat["pred_score"] = model.model.predict_proba(X)[:, 1]
    df_feat["expected_value"] = df_feat["pred_score"] * df_feat["odds"]
    df_feat["is_ana"] = (df_feat["expected_value"] >= 1.5) & (df_feat["odds"] >= 8.0)

    # Sort within race
    df_sorted = df_feat.sort_values(["race_id", "pred_score"], ascending=[True, False]).copy()

    # Vectorized extraction of top 1 and top 2
    top1 = df_sorted.groupby("race_id").nth(0).reset_index()
    top2 = df_sorted.groupby("race_id").nth(1).reset_index()

    m = top1[[
        "race_id", "date", "race_course", "race_number", "race_name",
        "surface", "distance", "horse_number", "pred_score", "odds",
        "expected_value", "finish_order"
    ]].rename(
        columns={
            "horse_number": "h1",
            "pred_score": "s1",
            "odds": "o1",
            "expected_value": "ev1",
            "finish_order": "fo1"
        }
    )

    top2_sub = top2[["race_id", "horse_number", "odds", "finish_order"]].rename(
        columns={"horse_number": "h2", "odds": "o2", "finish_order": "fo2"}
    )
    m = m.merge(top2_sub, on="race_id", how="left")

    ana = df_sorted[df_sorted["is_ana"]].groupby("race_id")["odds"].max().reset_index().rename(
        columns={"odds": "max_ana_odds"}
    )
    m = m.merge(ana, on="race_id", how="left")
    m["max_ana_odds"] = m["max_ana_odds"].fillna(0.0)

    est_umaren = m["o1"] * m["o2"].fillna(1.0) * 0.55
    m["max_return_odds"] = np.maximum(m["o1"], np.maximum(m["max_ana_odds"], est_umaren))

    c_ss = m["s1"] >= 0.82
    c_s = m["s1"] >= 0.75
    c_a = m["s1"] >= 0.65

    conditions = [c_ss, c_s, c_a]
    choices = ["🔥【SS】", "⚡【S】", "⭐【A】"]
    m["rank_tag"] = np.select(conditions, choices, default="【B】")

    top3_finish = df_sorted[df_sorted["finish_order"] <= 3].groupby("race_id")["horse_number"].apply(set).reset_index().rename(
        columns={"horse_number": "top3_nums"}
    )
    m = m.merge(top3_finish, on="race_id", how="left")

    def check_hit(row):
        date_str = str(row["date"])[:10]
        if date_str > "2026-10-23":
            return "【発走前⏳】"
        nums = row["top3_nums"] if isinstance(row["top3_nums"], set) else set()
        hit_tan = (row["fo1"] == 1)
        hit_fuku = (row["fo1"] <= 3)
        hit_wide = (row["h1"] in nums) and (row["h2"] in nums if pd.notna(row["h2"]) else False)
        if hit_tan or hit_wide or hit_fuku:
            return "【的中🎯】"
        else:
            return "【不的中❌】"

    m["hit_tag"] = m.apply(check_hit, axis=1)
    m["rank_order"] = m["rank_tag"].map({"🔥【SS】": 1, "⚡【S】": 2, "⭐【A】": 3, "【B】": 4}).fillna(5).astype(int)

    def make_label(r):
        d_str = str(r["date"])[:10]
        r_name = str(r["race_name"])
        prefix = f"{r['race_course']}{r['race_number']}R"
        if r_name.startswith(prefix):
            clean_name = r_name
        else:
            clean_name = f"{prefix} {r_name}"
        return f"{r['rank_tag']} {r['hit_tag']} 【{d_str}】{clean_name} ({r['surface']}{r['distance']}m)"

    m["dropdown_label"] = m.apply(make_label, axis=1)

    cache_df = m[[
        "race_id", "date", "rank_tag", "hit_tag", "rank_order", "dropdown_label"
    ]].copy()
    cache_path = Path("data/race_rank_cache.parquet")
    cache_df.to_parquet(cache_path, index=False)
    print(f"Saved {cache_path} ({len(cache_df)} races in {time.time() - t0:.1f}s).")

    print("\n--- サンプル（直近・発走前） ---")
    for lbl in m[m["date"] >= "2026-10-24"]["dropdown_label"].head(5):
        print(lbl)

    print("\n--- サンプル（過去・確定） ---")
    for lbl in m[m["date"] == "2026-10-18"]["dropdown_label"].head(5):
        print(lbl)

if __name__ == "__main__":
    main()
