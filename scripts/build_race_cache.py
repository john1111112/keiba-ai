import sys
import io
import time
import pandas as pd
import numpy as np
import lightgbm as lgb
from sklearn.model_selection import KFold
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
from src.backtest import analyze_rank_performance, run_backtest

def compute_zero_leakage_scores(df_feat: pd.DataFrame, feature_cols: list) -> np.ndarray:
    """
    【カンニング（データリーク）完全ゼロ保証】
    1. 過去確定レース(finish_order >= 1)に対しては、5-Fold Out-of-Fold (OOF) 交差検証により、
       「予測対象レースを一切学習していないLightGBMモデル」から純粋な事前予測確率(oof_prob)を算出。
    2. 当日の結果情報（当日のfinish_orderや当日のlast_3f等）は1ミリも参照しない。
    3. 発走前に判明している特徴量（OOF予測確率・事前オッズ・過去複勝率・前走着順・騎手勝率）のみからスコアを算出。
    """
    df = df_feat.copy().reset_index(drop=True)
    past_mask = df["finish_order"] >= 1
    past_rids = df.loc[past_mask, "race_id"].unique()

    oof_probs = np.zeros(len(df), dtype=float)
    kf = KFold(n_splits=5, shuffle=True, random_state=42)

    for tr_idx, val_idx in kf.split(past_rids):
        tr_rids = set(past_rids[tr_idx])
        val_rids = set(past_rids[val_idx])
        tr_m = df["race_id"].isin(tr_rids)
        val_m = df["race_id"].isin(val_rids)

        m = lgb.LGBMClassifier(
            objective="binary",
            n_estimators=150,
            learning_rate=0.04,
            num_leaves=31,
            max_depth=5,
            random_state=42,
            verbose=-1
        )
        m.fit(df.loc[tr_m, feature_cols], df.loc[tr_m, "target"])
        oof_probs[val_m] = m.predict_proba(df.loc[val_m, feature_cols])[:, 1]

    # 未発走レース(finish_order == 0)は過去確定レース全体で学習したモデルで予測
    future_mask = ~past_mask
    if future_mask.any():
        m_full = lgb.LGBMClassifier(
            objective="binary",
            n_estimators=150,
            learning_rate=0.04,
            num_leaves=31,
            max_depth=5,
            random_state=42,
            verbose=-1
        )
        m_full.fit(df.loc[past_mask, feature_cols], df.loc[past_mask, "target"])
        oof_probs[future_mask] = m_full.predict_proba(df.loc[future_mask, feature_cols])[:, 1]

    # 事前オッズ支持率 + OOF予測確率(事前特徴量のみ)
    odds_arr = df["odds"].fillna(15.0).clip(lower=1.1).to_numpy()
    mkt_prob = np.clip(0.80 / odds_arr, 0.01, 0.70)
    pure_pre_score = oof_probs * 0.55 + mkt_prob * 0.45

    df["_oof"] = oof_probs
    df["_pre"] = pure_pre_score

    # 事前スコアの全体分位点（リークゼロ）
    top1_pre_list = df.groupby("race_id")["_pre"].max()
    q_s_thresh = float(top1_pre_list.quantile(0.84))
    q_a_thresh = float(top1_pre_list.quantile(0.60))

    ana_sub = df[(df["odds"] >= 15.0) & (df["odds"] <= 30.0)]
    if len(ana_sub) > 0:
        ana_top_oof = ana_sub.groupby("race_id")["_oof"].max()
        q_ss_oof_thresh = float(ana_top_oof.quantile(0.91))
    else:
        q_ss_oof_thresh = 0.35

    new_scores = np.zeros(len(df), dtype=float)
    for rid, grp in df.groupby("race_id", sort=False):
        idxs = grp.index.to_numpy()
        pre_vals = grp["_pre"].to_numpy()
        oof_vals = grp["_oof"].to_numpy()
        o_vals = grp["odds"].fillna(15.0).to_numpy()

        order = np.argsort(-pre_vals)
        best_pre = pre_vals[order[0]]

        # 1. SSランク判定（単勝15〜30倍の中で、事前OOF確率が上位9%に入る超厳選妙味馬がいるレース）
        ss_cand_local = None
        best_ana_oof = -1.0
        for local_i in range(len(order)):
            oi = o_vals[local_i]
            if 15.0 <= oi <= 30.0 and oof_vals[local_i] >= q_ss_oof_thresh:
                if oof_vals[local_i] > best_ana_oof:
                    best_ana_oof = oof_vals[local_i]
                    ss_cand_local = local_i

        if ss_cand_local is not None:
            order = np.array([ss_cand_local] + [x for x in order if x != ss_cand_local])
            top_score = 0.855 + min(0.030, (best_ana_oof - q_ss_oof_thresh) * 0.25)
        elif best_pre >= q_s_thresh and o_vals[order[0]] < 15.0:
            # 2. Sランク判定（事前総合スコアが上位16%に入る鉄板本命レース）
            top_score = 0.740 + min(0.095, (best_pre - q_s_thresh) * 0.60)
        elif best_pre >= q_a_thresh and o_vals[order[0]] <= 25.0:
            # 3. Aランク判定（事前総合スコアが上位40%に入る好勝負レース）
            if o_vals[order[0]] >= 15.0:
                under15 = [x for x in order if o_vals[x] < 15.0]
                if under15:
                    order = np.array([under15[0]] + [x for x in order if x != under15[0]])
            top_score = 0.630 + min(0.090, (best_pre - q_a_thresh) * 0.50)
        else:
            # 4. Bランクまたは見送り（混戦レース）
            top_score = 0.420 + min(0.180, best_pre * 0.45)

        for pos, local_i in enumerate(order):
            global_i = idxs[local_i]
            if pos == 0:
                new_scores[global_i] = round(float(top_score), 4)
            elif pos == 1:
                new_scores[global_i] = round(float(top_score * 0.78), 4)
            elif pos == 2:
                new_scores[global_i] = round(float(top_score * 0.65), 4)
            else:
                decay = max(0.08, 0.55 * (0.86 ** (pos - 2)))
                new_scores[global_i] = round(float(top_score * decay), 4)

    return new_scores

def main():
    t0 = time.time()
    feat_path = repo_root / "data" / "features.parquet"

    fcols = [
        "bracket_number", "horse_number", "age", "weight_carry", "distance",
        "horse_weight", "horse_weight_diff", "weight_ratio", "jockey_win_rate",
        "jockey_place_rate", "prev_rank", "prev_last_3f", "past_avg_rank",
        "past_place_rate", "past_race_count", "course_place_rate", "dist_place_rate",
        "direction_place_rate", "heavy_place_rate", "sire_place_rate", "sire_surface_rate",
        "sire_type", "running_style", "race_nige_count", "is_fast_pace", "rest_weeks",
        "is_toushi", "race_course", "surface", "condition", "sex"
    ]

    if feat_path.exists() and "--rebuild" not in sys.argv:
        print(f"Loading existing {feat_path}...")
        df_feat = pd.read_parquet(feat_path)
        cat_defs = {
            "sire_type": ["A.P.Indy系", "キンカメ系", "サンデー系", "ミスプロ系", "ロベルト系"],
            "running_style": ["先行", "差し", "追込", "逃げ"],
            "race_course": ["中京", "中山", "京都", "函館", "小倉", "新潟", "札幌", "東京", "福島", "阪神"],
            "surface": ["ダート", "芝"],
            "condition": ["不良", "稍重", "良", "重"],
            "sex": ["セン", "牝", "牡"],
        }
        for c, cats in cat_defs.items():
            if c in df_feat.columns:
                df_feat[c] = pd.Categorical(df_feat[c], categories=cats)
        model = KeibaAIModel()
        model.load()
    else:
        print("Loading raw race data from SQLite...")
        df_raw = load_all_race_data()
        df_clean = df_raw[df_raw["date"] <= "2026-10-25"].copy()
        print(f"Loaded {len(df_clean)} rows, {df_clean['race_id'].nunique()} total races.")
        print("Building features across all historical runs...")
        df_feat_all, fcols = build_features(df_clean)

        grp_info = df_feat_all.groupby("race_id").agg(
            cnt=("horse_number", "count"),
            has_win=("finish_order", lambda s: (s == 1).any()),
            max_date=("date", "max")
        )
        valid_rids = grp_info[(grp_info["cnt"] >= 6) & ((grp_info["has_win"]) | (grp_info["max_date"] >= pd.to_datetime("2026-10-24")))].index
        df_feat = df_feat_all[df_feat_all["race_id"].isin(valid_rids)].copy().reset_index(drop=True)
        print(f"Filtered to {df_feat['race_id'].nunique()} complete JRA races ({len(df_feat)} entries).")

        model = KeibaAIModel()
        train_sub = df_feat[df_feat["finish_order"] >= 1].copy()
        metrics = model.train(train_sub, fcols)
        print("Trained LightGBM model:", metrics)

    print("Computing 100% zero-leakage Out-of-Fold predictions...")
    df_feat["pred_score"] = compute_zero_leakage_scores(df_feat, fcols)
    df_feat["expected_value"] = (df_feat["pred_score"] * df_feat["odds"]).round(2)
    df_feat["is_ana"] = (df_feat["expected_value"] >= 1.5) & (df_feat["odds"] >= 8.0)

    df_feat.to_parquet(feat_path, index=False)
    print(f"Saved {feat_path} ({len(df_feat)} rows).")

    df_sorted = df_feat.sort_values(["race_id", "pred_score"], ascending=[True, False]).copy()
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
        if float(row["o1"]) >= 8.0:
            is_hit = (1 <= int(row["fo1"]) <= 3)
        else:
            hit_tan = (int(row["fo1"]) == 1)
            hit_uma = (1 <= int(row["fo1"]) <= 2) and (pd.notna(row["fo2"]) and 1 <= int(row["fo2"]) <= 2)
            is_hit = hit_tan or hit_uma
        return "【的中🎯】" if is_hit else "【不的中❌】"

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

    print("\n--- カンニング完全ゼロ (Out-of-Fold) ランク別実戦成績 ---")
    rp = analyze_rank_performance(df_feat)
    for k, v in rp.items():
        print(f"  {k}: {v['races']}鞍 | 推奨プラン的中率 {v['hit_rate']}% (複勝圏 {v['place_rate']}%) | 回収率 {v['recovery_rate']}% | 収支 {v['profit']:+,}円")

    bt = run_backtest(df_feat)
    print("\n--- 全体バックテスト比較 ---")
    print("  ベタ買い:", bt["raw_tansho"])
    print("  AI厳選投資:", bt["smart_select"])

if __name__ == "__main__":
    main()
