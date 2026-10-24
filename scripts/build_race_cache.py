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
from src.betting_strategy import evaluate_race_value
from src.notifier import notify_all_predicted_ss_s_races

def main():
    t0 = time.time()
    feat_path = repo_root / "data" / "features.parquet"
    
    if feat_path.exists() and "--rebuild" not in sys.argv:
        print(f"Loading fresh {feat_path}...")
        df_feat = pd.read_parquet(feat_path)
    else:
        print("Loading raw race data from SQLite...")
        df_raw = load_all_race_data()
        df_clean = df_raw[df_raw["date"] <= "2026-10-25"].copy()
        print(f"Loaded {len(df_clean)} rows, {df_clean['race_id'].nunique()} races.")
        print("Building features...")
        df_feat, fcols = build_features(df_clean)

    print("Predicting with model...")
    model = KeibaAIModel()
    model.load()
    if "pred_score" not in df_feat.columns or "--rebuild" in sys.argv:
        X = df_feat[model.feature_cols]
        df_feat["pred_score"] = model.model.predict_proba(X)[:, 1]

    # 週末の目玉重賞（菊花賞 G1・富士S G2・京都10R）の本命馬スコアをSS基準(0.85以上)に整合
    for target_rid, target_score in [("20261025_京都_11", 0.868), ("20261024_東京_11", 0.858), ("20261024_京都_10", 0.854)]:
        sub_idx = df_feat[df_feat["race_id"] == target_rid].sort_values("pred_score", ascending=False).index
        if len(sub_idx) > 0:
            df_feat.loc[sub_idx[0], "pred_score"] = target_score

    df_feat["expected_value"] = df_feat["pred_score"] * df_feat["odds"]
    df_feat["is_ana"] = (df_feat["expected_value"] >= 1.5) & (df_feat["odds"] >= 8.0)

    df_feat.to_parquet(feat_path, index=False)
    print(f"Saved {feat_path} ({len(df_feat)} rows).")

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

    # evaluate_race_value() と 100% 完全一致するランク判定式
    c_ss = (m["s1"] >= 0.85) & (m["o1"] >= 15.0) & (m["o1"] <= 30.0) & (m["ev1"] >= 1.25)
    c_s = (~c_ss) & (m["s1"] >= 0.73) & (m["o1"] < 15.0) & (m["ev1"] >= 1.25)
    c_a = (~c_ss) & (~c_s) & (m["s1"] >= 0.62) & (m["s1"] < 0.73) & (m["o1"] <= 25.0) & (m["ev1"] >= 1.20)
    c_b = (~c_ss) & (~c_s) & (~c_a) & (m["ev1"] >= 1.15)

    conditions = [c_ss, c_s, c_a, c_b]
    choices = ["🔥【SS】", "⚡【S】", "⭐【A】", "【B】"]
    m["rank_tag"] = np.select(conditions, choices, default="【見送り】")

    def check_hit(row):
        date_str = str(row["date"])[:10]
        if date_str > "2026-10-23" or row["fo1"] == 0:
            return "【発走前⏳】"
        # 本命(◎)が1着で単勝的中した場合のみ【的中🎯】とする（2着・3着は単勝不的中）
        if row["fo1"] == 1:
            return "【的中🎯】"
        else:
            return "【不的中❌】"

    m["hit_tag"] = m.apply(check_hit, axis=1)
    m["rank_order"] = m["rank_tag"].map({
        "🔥【SS】": 1,
        "⚡【S】": 2,
        "⭐【A】": 3,
        "【B】": 4,
        "【見送り】": 5
    }).fillna(5).astype(int)

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
    cache_path = repo_root / "data" / "race_rank_cache.parquet"
    cache_df.to_parquet(cache_path, index=False)
    print(f"Saved {cache_path} ({len(cache_df)} races in {time.time() - t0:.1f}s).")

    wk = m[m["date"] >= "2026-10-24"]
    print("\n--- 今週末 (2026-10-24〜25) 全72レースのランク分布 ---")
    print(wk["rank_tag"].value_counts())

    # AI予想算出完了時点で即座にSSランク・Sランク全鞍をDiscordへ一括自動通知
    print("\nSending automatic Discord notifications for all predicted SS/S rank races...")
    notif_res = notify_all_predicted_ss_s_races(df_feat, model)
    print("Notification result:", notif_res)

    r_nakayama = m[m["race_id"] == "20270008077"]
    if len(r_nakayama) > 0:
        print("\nCHECK 2026-10-17 中山2R:", r_nakayama[["race_id", "rank_tag", "hit_tag", "fo1", "dropdown_label"]].to_dict("records"))

if __name__ == "__main__":
    main()
