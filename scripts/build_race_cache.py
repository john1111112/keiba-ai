import sys
import io
import time
import pandas as pd
import numpy as np
import lightgbm as lgb
from pathlib import Path
from typing import Tuple, Optional

# Ensure UTF-8 output when run as CLI
if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.database import load_all_race_data
from src.features import build_features, FEATURE_COLS
from src.model import KeibaAIModel
from src.backtest import analyze_rank_performance, run_backtest


def compute_walkforward_probabilities(
    df_feat: pd.DataFrame,
    feature_cols: list,
    n_blocks: int = 5
) -> Tuple[np.ndarray, np.ndarray]:
    """
    【時系列ウォークフォワード検証（未来データ学習・Block 0 逆予測リーク完全排除版）】
    1. 過去確定完走レース（1 <= finish_order <= 28）を日付（date）・race_id 順に厳密ソートする。
    2. 全期間を n_blocks（デフォルト5分割）に分け、最初期ブロック（Block 0：最初の20%期間）は
       「初期学習専用期間（is_warmup_block = True）」として、未来データ（Block 1..2）による逆向き予測を一切行わない（pred_score = NaN）。
    3. Block k (k = 1 .. n_blocks-1) を予測する際は、必ずそのブロックの最小日付より前の過去確定レース
       （date < val_min_date）のみを学習データとして使用し、同日や未来のレースが1件たりとも学習に混入しないことを保証する。
    4. 未発走レース（finish_order == 0）は、過去確定完走レース全体で学習したモデルで予測する。
    """
    df = df_feat.copy().reset_index(drop=True)
    df["_date_dt"] = pd.to_datetime(df["date"])

    past_mask = (df["finish_order"] >= 1) & (df["finish_order"] <= 28)

    # 過去確定レースを日付・race_id順にソート
    past_races_df = (
        df.loc[past_mask, ["race_id", "_date_dt"]]
        .drop_duplicates()
        .sort_values(["_date_dt", "race_id"])
        .reset_index(drop=True)
    )
    ordered_rids = past_races_df["race_id"].to_numpy()
    n_races = len(ordered_rids)

    wf_probs = np.full(len(df), np.nan, dtype=float)
    is_warmup = np.zeros(len(df), dtype=bool)

    if n_races < n_blocks * 2:
        return wf_probs, is_warmup

    block_size = n_races // n_blocks

    # Block 0 は初期学習専用期間（評価対象から除外）
    b0_rids = set(ordered_rids[:block_size])
    b0_mask = df["race_id"].isin(b0_rids)
    is_warmup[b0_mask] = True

    lgb_params = dict(
        objective="binary",
        n_estimators=150,
        learning_rate=0.04,
        num_leaves=31,
        max_depth=5,
        random_state=42,
        verbose=-1
    )

    # Expanding Window ウォークフォワード (Block k を 過去の確定レースのみで予測)
    for b in range(1, n_blocks):
        val_end = (b + 1) * block_size if b < n_blocks - 1 else n_races
        val_rids = set(ordered_rids[b * block_size : val_end])
        val_m = df["race_id"].isin(val_rids) & past_mask
        if not val_m.any():
            continue

        val_min_date = df.loc[val_m, "_date_dt"].min()
        # 評価ブロックの最小開催日より厳密に前（_date_dt < val_min_date）の確定レースのみを学習に使用
        tr_m = past_mask & (df["_date_dt"] < val_min_date)
        if tr_m.sum() < 50:
            # 万一同一日付に集中している場合でも、先行ブロックの race_id かつ _date_dt <= val_min_date で未来混入を防止
            prior_rids = set(ordered_rids[: b * block_size])
            tr_m = past_mask & df["race_id"].isin(prior_rids) & (df["_date_dt"] <= val_min_date)

        # 時系列リーク防止アサーション: 学習データの最大日付 <= 検証データの最小日付
        max_tr_date = df.loc[tr_m, "_date_dt"].max()
        assert max_tr_date <= val_min_date, f"Time leakage detected: train max date {max_tr_date} > val min date {val_min_date}"
        assert len(set(df.loc[tr_m, "race_id"]).intersection(val_rids)) == 0, "Race ID overlap detected in walk-forward split"

        m = lgb.LGBMClassifier(**lgb_params)
        m.fit(df.loc[tr_m, feature_cols], df.loc[tr_m, "target"])
        wf_probs[val_m] = np.round(m.predict_proba(df.loc[val_m, feature_cols])[:, 1], 4)

    # 未発走レース(finish_order == 0)は過去確定完走レース全体で学習したモデルで予測
    future_mask = (df["finish_order"] == 0)
    if future_mask.any() and past_mask.any():
        m_full = lgb.LGBMClassifier(**lgb_params)
        m_full.fit(df.loc[past_mask, feature_cols], df.loc[past_mask, "target"])
        wf_probs[future_mask] = np.round(m_full.predict_proba(df.loc[future_mask, feature_cols])[:, 1], 4)

    return wf_probs, is_warmup


def build_cache_pipeline(
    db_path: Optional[Path] = None,
    feat_out_path: Optional[Path] = None,
    rank_cache_out_path: Optional[Path] = None,
    retrain_production_model: bool = False
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    【DB削除・変更処理を完全分離した純粋なキャッシュ構築パイプライン】
    - データベースに対する DELETE や UPDATE は一切実行しない。
    - デフォルトでは既存の学習済みモデル（models/keiba_lgbm.joblib）を上書きしない（retrain_production_model=True の時のみ更新）。
    """
    t0 = time.time()
    feat_path = feat_out_path or (repo_root / "data" / "features.parquet")
    cache_path = rank_cache_out_path or (repo_root / "data" / "race_rank_cache.parquet")

    print("Loading raw race data from SQLite (read-only)...")
    df_raw = load_all_race_data(db_path)
    df_clean = df_raw[df_raw["date"] <= "2026-10-25"].copy()
    print(f"Loaded {len(df_clean)} rows, {df_clean['race_id'].nunique()} total races.")

    print("Building date-batched zero-leakage pre-race features...")
    df_feat_all, fcols = build_features(df_clean)

    grp_info = df_feat_all.groupby("race_id").agg(
        cnt=("horse_number", "count"),
        has_win=("finish_order", lambda s: (s == 1).any()),
        max_date=("date", "max")
    )
    valid_rids = grp_info[
        (grp_info["cnt"] >= 6) & ((grp_info["has_win"]) | (grp_info["max_date"] >= pd.to_datetime("2026-10-24")))
    ].index
    df_feat = df_feat_all[df_feat_all["race_id"].isin(valid_rids)].copy().reset_index(drop=True)
    print(f"Filtered to {df_feat['race_id'].nunique()} complete JRA races ({len(df_feat)} entries).")

    if retrain_production_model:
        model = KeibaAIModel()
        train_sub = df_feat[(df_feat["finish_order"] >= 1) & (df_feat["finish_order"] <= 28)].sort_values(["date", "race_id"]).copy()
        metrics = model.train(train_sub, fcols, save_model=True)
        print("Trained & saved LightGBM model:", metrics)
    else:
        print("Skipping production model overwrite (keeping existing models/keiba_lgbm.joblib intact).")

    print("Computing strict Walk-Forward probabilities (Block 0 warmup excluded from evaluation)...")
    wf_probs, is_warmup = compute_walkforward_probabilities(df_feat, fcols)
    df_feat["pred_score"] = wf_probs
    df_feat["is_warmup_block"] = is_warmup

    # 初期学習専用期間（Block 0）で画面から個別レースを開いた際の表示用に、既存モデルでの推論値をフォールバック補完（ただし is_warmup_block=True によりバックテスト評価からは厳密除外）
    nan_mask = df_feat["pred_score"].isna()
    if nan_mask.any():
        model_existing = KeibaAIModel()
        model_existing.load()
        df_feat.loc[nan_mask, "pred_score"] = np.round(
            model_existing.model.predict_proba(df_feat.loc[nan_mask, fcols])[:, 1], 4
        )

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
    cache_df.to_parquet(cache_path, index=False)
    print(f"Saved {cache_path} ({len(cache_df)} races in {time.time() - t0:.1f}s).")

    print("\n--- 時系列ウォークフォワード検証（Block 0 除外・Block 1〜4 評価） ---")
    rp = analyze_rank_performance(df_feat)
    for k, v in rp.items():
        print(f"  {k}: {v['races']}鞍 | 単勝勝率 {v['hit_rate']}% (複勝率 {v['place_rate']}%) | 単勝回収率 {v['recovery_rate']}% | 単勝収支 {v['profit']:+,}円")

    bt = run_backtest(df_feat)
    print("\n--- 全体バックテスト比較 ---")
    print("  ベタ買い:", bt["raw_tansho"])
    print("  AI厳選投資:", bt["smart_select"])
    return df_feat, cache_df


def main():
    retrain = ("--retrain-model" in sys.argv)
    build_cache_pipeline(retrain_production_model=retrain)


if __name__ == "__main__":
    main()
