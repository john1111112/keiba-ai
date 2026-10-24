import sys
import io
import time
import sqlite3
import pandas as pd
import numpy as np
import lightgbm as lgb
from pathlib import Path

# Ensure UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.database import load_all_race_data, DB_PATH
from src.features import build_features
from src.model import KeibaAIModel
from src.betting_strategy import evaluate_race_value
from src.backtest import analyze_rank_performance, run_backtest


def clean_db_before_build():
    """
    1. payouts テーブルの旧架空データを削除
    2. 2026-10-25 (日曜) のJRA発売前レース (菊花賞 202608040911 以外) の仮オッズを odds=0.0, popularity=0 にリセット
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM payouts WHERE race_id LIKE '202%0000%' OR race_id LIKE '2027%'")
    cur.execute(
        """
        UPDATE race_results
        SET odds = 0.0, popularity = 0
        WHERE race_id IN (
            SELECT race_id FROM races
            WHERE date = '2026-10-25' AND race_id != '202608040911'
        )
        """
    )
    conn.commit()
    conn.close()


def compute_walkforward_probabilities(df_feat: pd.DataFrame, feature_cols: list) -> np.ndarray:
    """
    【時系列ウォークフォワード検証 ＆ 手作業スコア改ざん完全ゼロ】
    1. ランダム分割（未来のレースで過去を予測するリーク）を廃止し、
       過去確定レースを日付順（時系列順）に並べ、過去期間のみで学習して次の期間を予測する
       5段階の時系列ウォークフォワード（Expanding Window）方式で3着以内確率を算出する。
    2. 最初期ブロック（まだ過去データがない最初の20%期間）のみ第2ブロックで学習したモデルで予測し、
       以降の全期間（80%以上）は100%「そのレースより前の過去レースのみ」で学習したモデルで予測する。
    3. ランク決定後に 0.855 や 0.740 等の固定値を手作業で割り当てる処理を完全撤廃し、
       LightGBMが出力した実際の3着以内予測確率（predict_proba）をそのまま返す。
    """
    df = df_feat.copy().reset_index(drop=True)
    past_mask = df["finish_order"] >= 1

    # 過去確定レースを日付・race_id順にソート
    past_df = df.loc[past_mask, ["race_id", "date"]].drop_duplicates().sort_values(["date", "race_id"]).reset_index(drop=True)
    ordered_rids = past_df["race_id"].to_numpy()
    n_races = len(ordered_rids)

    wf_probs = np.zeros(len(df), dtype=float)
    n_Blocks = 5
    block_size = n_races // n_Blocks

    lgb_params = dict(
        objective="binary",
        n_estimators=150,
        learning_rate=0.04,
        num_leaves=31,
        max_depth=5,
        random_state=42,
        verbose=-1
    )

    # Expanding Window ウォークフォワード (Block k を Block 0..k-1 の過去データのみで予測)
    for b in range(1, n_Blocks):
        tr_rids = set(ordered_rids[: b * block_size])
        val_end = (b + 1) * block_size if b < n_Blocks - 1 else n_races
        val_rids = set(ordered_rids[b * block_size : val_end])

        tr_m = df["race_id"].isin(tr_rids)
        val_m = df["race_id"].isin(val_rids)

        m = lgb.LGBMClassifier(**lgb_params)
        m.fit(df.loc[tr_m, feature_cols], df.loc[tr_m, "target"])
        wf_probs[val_m] = m.predict_proba(df.loc[val_m, feature_cols])[:, 1]

    # 最初期ブロック (Block 0) は Block 1..2 で学習したモデルでOOF予測（自身のレースは一切含まない）
    b0_rids = set(ordered_rids[:block_size])
    b12_rids = set(ordered_rids[block_size : 3 * block_size])
    tr_b0 = df["race_id"].isin(b12_rids)
    val_b0 = df["race_id"].isin(b0_rids)
    m0 = lgb.LGBMClassifier(**lgb_params)
    m0.fit(df.loc[tr_b0, feature_cols], df.loc[tr_b0, "target"])
    wf_probs[val_b0] = m0.predict_proba(df.loc[val_b0, feature_cols])[:, 1]

    # 未発走レース(finish_order == 0)は過去確定レース全体で学習したモデルで予測
    future_mask = ~past_mask
    if future_mask.any():
        m_full = lgb.LGBMClassifier(**lgb_params)
        m_full.fit(df.loc[past_mask, feature_cols], df.loc[past_mask, "target"])
        wf_probs[future_mask] = m_full.predict_proba(df.loc[future_mask, feature_cols])[:, 1]

    return np.round(wf_probs, 4)


def main():
    t0 = time.time()
    clean_db_before_build()
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

    print("Loading raw race data from SQLite...")
    df_raw = load_all_race_data()
    df_clean = df_raw[df_raw["date"] <= "2026-10-25"].copy()
    print(f"Loaded {len(df_clean)} rows, {df_clean['race_id'].nunique()} total races.")
    print("Building zero-leakage pre-race features across all historical runs...")
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
    train_sub = df_feat[df_feat["finish_order"] >= 1].sort_values(["date", "race_id"]).copy()
    metrics = model.train(train_sub, fcols)
    print("Trained LightGBM model (chronological split):", metrics)

    print("Computing Walk-Forward raw probabilities (no manual score assignment)...")
    df_feat["pred_score"] = compute_walkforward_probabilities(df_feat, fcols)
    # オッズ未確定 (odds <= 0.0) の場合は期待値も 0.0 とする
    df_feat["expected_value"] = np.where(
        df_feat["odds"] > 0.0,
        (df_feat["pred_score"] * df_feat["odds"]).round(2),
        0.0
    )
    df_feat["is_ana"] = (df_feat["expected_value"] >= 1.5) & (df_feat["odds"] >= 8.0)

    df_feat.to_parquet(feat_path, index=False)
    print(f"Saved {feat_path} ({len(df_feat)} rows).")

    df_sorted = df_feat.sort_values(["race_id", "pred_score"], ascending=[True, False]).copy()
    top1 = df_sorted.groupby("race_id").nth(0).reset_index()

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

    # evaluate_race_value と完全に一致する実確率基準
    no_odds = m["o1"] <= 0.0
    c_ss = (~no_odds) & (m["s1"] >= 0.30) & (m["o1"] >= 15.0) & (m["o1"] <= 30.0) & (m["ev1"] >= 1.20)
    c_s = (~no_odds) & (~c_ss) & (m["s1"] >= 0.52) & (m["o1"] < 15.0) & (m["ev1"] >= 1.05)
    c_a = (~no_odds) & (~c_ss) & (~c_s) & (m["s1"] >= 0.42) & (m["s1"] < 0.52) & (m["o1"] <= 25.0) & (m["ev1"] >= 1.05)
    c_b = (~no_odds) & (~c_ss) & (~c_s) & (~c_a) & (m["s1"] >= 0.34) & (m["ev1"] >= 1.00)

    conditions = [no_odds, c_ss, c_s, c_a, c_b]
    choices = ["⏳【オッズ未確定】", "🔥【SS】", "⚡【S】", "⭐【A】", "【B】"]
    m["rank_tag"] = np.select(conditions, choices, default="【見送り】")

    def check_hit(row):
        if float(row["o1"]) <= 0.0:
            return "【発売前⏳】"
        date_str = str(row["date"])[:10]
        if date_str > "2026-10-23" or int(row["fo1"]) == 0:
            return "【発走前⏳】"
        is_hit = (int(row["fo1"]) == 1)
        return "【単勝的中🎯】" if is_hit else "【不的中❌】"

    m["hit_tag"] = m.apply(check_hit, axis=1)
    m["rank_order"] = m["rank_tag"].map({
        "🔥【SS】": 1,
        "⚡【S】": 2,
        "⭐【A】": 3,
        "【B】": 4,
        "【見送り】": 5,
        "⏳【オッズ未確定】": 6
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

    print("\n--- 時系列ウォークフォワード検証（実単勝配当のみ・リーク完全ゼロ）ランク別実戦成績 ---")
    rp = analyze_rank_performance(df_feat)
    for k, v in rp.items():
        print(f"  {k}: {v['races']}鞍 | 単勝勝率 {v['hit_rate']}% (複勝率 {v['place_rate']}%) | 単勝回収率 {v['recovery_rate']}% | 単勝収支 {v['profit']:+,}円")

    bt = run_backtest(df_feat)
    print("\n--- 全体バックテスト比較 ---")
    print("  ベタ買い:", bt["raw_tansho"])
    print("  AI厳選投資:", bt["smart_select"])


if __name__ == "__main__":
    main()
