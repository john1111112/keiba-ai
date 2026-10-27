import sys
import io
import time
import pandas as pd
import numpy as np
import lightgbm as lgb
from pathlib import Path
from typing import Tuple, Optional, List

if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.database import load_all_race_data, evaluate_race_completeness_and_export_csv, INCOMPLETE_RACES_CSV_PATH
from src.features import build_features, get_feature_cols_by_version
from src.model import KeibaAIModel, MODEL_PATH, MODEL_V2_28_PATH
from src.backtest import analyze_rank_performance, run_backtest
from src.data_trust_gate import require_verified_race_data


def compute_walkforward_probabilities(
    df_feat: pd.DataFrame,
    feature_cols: list,
    n_blocks: int = 5
) -> Tuple[np.ndarray, np.ndarray]:
    """
    【Phase 3A: 開催日単位境界・厳密ウォークフォワード検証】
    1. 学習期間と評価期間の境界を「開催日（_date_dt）単位」に厳格化し、同一開催日が学習と評価に絶対にまたがらないようにする。
    2. 最初期ブロック（Block 0：最初の1ブロック分の開催日期間）は「初期学習専用期間（is_warmup_block = True）」として評価対象から完全除外する。
    3. 各評価ブロック（Block 1 .. n_blocks-1）では、必ず `学習データの最大日付 < 評価データの最小日付`（厳密不等号）を満たすことを必須条件とし、
       条件を満たせない場合は直ちに RuntimeError で処理を停止する。
    4. 評価期間のデータはモデル選択（early_stopping等）や閾値調整に一切使用しない。
    """
    df = df_feat.copy().reset_index(drop=True)
    df["_date_dt"] = pd.to_datetime(df["date"])

    t_series = df["time_str"].fillna("").astype(str).str.strip() if "time_str" in df.columns else pd.Series(["1:35.0"] * len(df))
    past_mask = (df["finish_order"] >= 1) & (df["finish_order"] <= 28) & (~t_series.isin(["", "-"]))

    # 過去確定レースの開催日一覧を昇順取得し、レース数が均等になるよう開催日単位の境界を決定する
    past_races_df = (
        df.loc[past_mask, ["race_id", "_date_dt"]]
        .drop_duplicates()
        .sort_values(["_date_dt", "race_id"])
        .reset_index(drop=True)
    )
    n_races = len(past_races_df)
    if n_races < n_blocks * 2:
        raise RuntimeError(f"ウォークフォワード検証に必要な確定レース数が不足しています (n_races={n_races})。")

    block_size = n_races // n_blocks
    boundary_dates: List[pd.Timestamp] = []
    for b in range(1, n_blocks):
        candidate_dt = past_races_df.loc[b * block_size, "_date_dt"]
        # もし境界インデックスの開催日が前ブロック末尾と同じ日付なら、次の異なる開催日を境界にして同一開催日またがりを防止
        prev_dt = past_races_df.loc[b * block_size - 1, "_date_dt"]
        if candidate_dt == prev_dt:
            later_dates = past_races_df.loc[past_races_df["_date_dt"] > candidate_dt, "_date_dt"]
            if len(later_dates) == 0:
                raise RuntimeError("ウォークフォワードの開催日境界を分離できません。")
            candidate_dt = later_dates.iloc[0]
        if boundary_dates and candidate_dt <= boundary_dates[-1]:
            later_dates = past_races_df.loc[past_races_df["_date_dt"] > boundary_dates[-1], "_date_dt"]
            if len(later_dates) == 0:
                raise RuntimeError("ウォークフォワードの開催日境界が単調増加になりません。")
            candidate_dt = later_dates.iloc[0]
        boundary_dates.append(candidate_dt)

    wf_probs = np.full(len(df), np.nan, dtype=float)
    is_warmup = np.zeros(len(df), dtype=bool)

    # Block 0 (date < boundary_dates[0]) は初期学習専用期間（評価対象から完全除外）
    b0_mask = past_mask & (df["_date_dt"] < boundary_dates[0])
    if not b0_mask.any():
        raise RuntimeError("Block 0（初期学習期間）が空のためウォークフォワード検証を停止します。")
    is_warmup[b0_mask] = True

    # ハイパーパラメータは固定（評価期間のデータは early_stopping 等にも一切使用しない）
    lgb_params = dict(
        objective="binary",
        n_estimators=150,
        learning_rate=0.04,
        num_leaves=31,
        max_depth=5,
        random_state=42,
        verbose=-1
    )

    for b in range(1, n_blocks):
        val_start_dt = boundary_dates[b - 1]
        if b < n_blocks - 1:
            val_end_dt = boundary_dates[b]
            val_m = past_mask & (df["_date_dt"] >= val_start_dt) & (df["_date_dt"] < val_end_dt)
        else:
            val_m = past_mask & (df["_date_dt"] >= val_start_dt)

        tr_m = past_mask & (df["_date_dt"] < val_start_dt)

        if not tr_m.any() or not val_m.any():
            raise RuntimeError(f"Block {b} の学習データまたは評価データが空です。")

        max_tr_date = df.loc[tr_m, "_date_dt"].max()
        min_val_date = df.loc[val_m, "_date_dt"].min()

        # 必須条件: 学習データの最大日付 < 評価データの最小日付（同一開催日のまたがりも一切禁止）
        if not (max_tr_date < min_val_date):
            raise RuntimeError(
                f"ウォークフォワード時系列違反により停止: train max date ({max_tr_date}) >= eval min date ({min_val_date})"
            )
        tr_dates = set(df.loc[tr_m, "_date_dt"].unique())
        val_dates = set(df.loc[val_m, "_date_dt"].unique())
        if len(tr_dates.intersection(val_dates)) > 0:
            raise RuntimeError("同一開催日が学習期間と評価期間にまたがっています。")

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
    incomplete_csv_out_path: Optional[Path] = None,
    feature_version: str = "v2_28cols_no_pedigree",
    retrain_production_model: bool = False,
    model_out_path: Optional[Path] = None
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    【Phase 3A: 固定日付撤去・不完全レースCSV出力・28特徴量対応・期待値誤計算廃止版】
    - 本番DB・本番モデル・本番キャッシュはデフォルトで一切上書きしない。
    """
    require_verified_race_data(db_path, purpose="training")
    t0 = time.time()
    feat_path = feat_out_path or (repo_root / "data" / "features.parquet")
    cache_path = rank_cache_out_path or (repo_root / "data" / "race_rank_cache.parquet")
    inc_csv_path = incomplete_csv_out_path or INCOMPLETE_RACES_CSV_PATH

    print("Loading raw race data from SQLite (read-only, no hardcoded dates)...")
    df_raw = load_all_race_data(db_path)
    print(f"Loaded {len(df_raw)} rows, {df_raw['race_id'].nunique()} total races.")

    print("Evaluating race completeness and exporting incomplete races report CSV...")
    comp_summary_df, incomplete_df = evaluate_race_completeness_and_export_csv(
        df_all=df_raw, db_path=db_path, csv_out_path=inc_csv_path
    )
    complete_eval_rids = set(comp_summary_df.loc[comp_summary_df["is_complete_for_eval"], "race_id"].astype(str))
    print(f"Complete races for training/evaluation: {len(complete_eval_rids)} (Incomplete excluded: {len(incomplete_df)} -> saved to {inc_csv_path})")

    print(f"Building date-batched zero-leakage pre-race features (version={feature_version})...")
    # 過去走履歴としては、不完全出馬表レース内の実在完走馬レコードも利用し、学習・評価対象は complete_eval_rids のみに厳密限定する
    df_feat_all, fcols = build_features(df_raw, feature_version=feature_version)
    df_feat = df_feat_all[df_feat_all["race_id"].astype(str).isin(complete_eval_rids)].copy().reset_index(drop=True)
    print(f"Filtered to {df_feat['race_id'].nunique()} complete JRA races ({len(df_feat)} entries).")

    if retrain_production_model or model_out_path is not None:
        target_m_path = model_out_path or (MODEL_V2_28_PATH if "28" in feature_version else MODEL_PATH)
        model = KeibaAIModel(model_path=target_m_path, feature_version=feature_version)
        train_sub = df_feat[df_feat["finish_order"] >= 1].sort_values(["date", "race_id"]).copy()
        metrics = model.train(train_sub, fcols, save_model=True)
        print(f"Trained & saved model ({target_m_path}):", metrics)
    else:
        print("Skipping production model overwrite (keeping existing models/keiba_lgbm.joblib intact).")

    print("Computing date-boundary Walk-Forward probabilities (Block 0 warmup excluded)...")
    wf_probs, is_warmup = compute_walkforward_probabilities(df_feat, fcols)
    df_feat["pred_score"] = wf_probs
    df_feat["is_warmup_block"] = is_warmup

    # 単勝期待値・複勝期待値の誤計算を廃止（算出不可として NaN を設定）
    df_feat["win_expected_value"] = np.nan
    df_feat["place_expected_value"] = np.nan
    df_feat["expected_value"] = np.nan
    df_feat["is_ana"] = False

    if feat_out_path is not None:
        df_feat.to_parquet(feat_path, index=False)
        print(f"Saved {feat_path} ({len(df_feat)} rows).")

    # プルダウン用ランクキャッシュ（Block 0 の表示用スコアが NaN の行は 0.0 でソート補助）
    df_sorted = df_feat.assign(_sort_s=df_feat["pred_score"].fillna(0.0)).sort_values(
        ["race_id", "_sort_s"], ascending=[True, False]
    ).copy()
    top1 = df_sorted.groupby("race_id").nth(0).reset_index()

    m = top1[[
        "race_id", "date", "race_course", "race_number", "race_name",
        "surface", "distance", "horse_number", "pred_score", "odds",
        "finish_order"
    ]].rename(
        columns={
            "horse_number": "h1",
            "pred_score": "s1",
            "odds": "o1",
            "finish_order": "fo1"
        }
    )

    no_odds = m["o1"] <= 0.0
    c_ss = (~no_odds) & (m["s1"] >= 0.30) & (m["o1"] >= 15.0) & (m["o1"] <= 30.0)
    c_s = (~no_odds) & (~c_ss) & (m["s1"] >= 0.52) & (m["o1"] < 15.0)
    c_a = (~no_odds) & (~c_ss) & (~c_s) & (m["s1"] >= 0.42) & (m["s1"] < 0.52) & (m["o1"] <= 25.0)
    c_b = (~no_odds) & (~c_ss) & (~c_s) & (~c_a) & (m["s1"] >= 0.34)

    conditions = [no_odds, c_ss, c_s, c_a, c_b]
    choices = ["⏳【オッズ未確定】", "🔥【SS(参考)】", "⚡【S(参考)】", "⭐【A(参考)】", "【B(参考)】"]
    m["rank_tag"] = np.select(conditions, choices, default="【見送り(参考)】")

    def check_hit(row):
        if float(row["o1"]) <= 0.0:
            return "【発売前⏳】"
        # 固定日付を撤去し、確定状態（fo1 >= 1）で判定
        if int(row["fo1"]) == 0:
            return "【発走前⏳】"
        is_hit = (int(row["fo1"]) == 1)
        return "【単勝的中🎯】" if is_hit else "【不的中❌】"

    m["hit_tag"] = m.apply(check_hit, axis=1)
    m["rank_order"] = m["rank_tag"].map({
        "🔥【SS(参考)】": 1,
        "⚡【S(参考)】": 2,
        "⭐【A(参考)】": 3,
        "【B(参考)】": 4,
        "【見送り(参考)】": 5,
        "⏳【オッズ未確定】": 6
    }).fillna(5).astype(int)

    def make_label(r):
        d_str = str(r["date"])[:10]
        r_name = str(r["race_name"])
        prefix = f"{r['race_course']}{r['race_number']}R"
        clean_name = r_name if r_name.startswith(prefix) else f"{prefix} {r_name}"
        return f"{r['rank_tag']} {r['hit_tag']} 【{d_str}】{clean_name} ({r['surface']}{r['distance']}m)"

    m["dropdown_label"] = m.apply(make_label, axis=1)

    cache_df = m[[
        "race_id", "date", "rank_tag", "hit_tag", "rank_order", "dropdown_label"
    ]].copy()
    if rank_cache_out_path is not None:
        cache_df.to_parquet(cache_path, index=False)
        print(f"Saved {cache_path} ({len(cache_df)} races in {time.time() - t0:.1f}s).")

    print("\n--- 開催日境界ウォークフォワード検証（Block 0 除外・Block 1〜4 評価） ---")
    rp = analyze_rank_performance(df_feat)
    for k, v in rp.items():
        print(f"  {k}: {v['races']}鞍 | 単勝勝率 {v['hit_rate']}% (複勝率 {v['place_rate']}%) | 単勝回収率 {v['recovery_rate']}% | 単勝収支 {v['profit']:+,}円")

    bt = run_backtest(df_feat)
    print("\n--- 全体バックテスト比較 ---")
    print("  ベタ買い:", bt["raw_tansho"])
    print("  旧ルール参考ランク(SS/S/A):", bt["smart_select"])
    return df_feat, cache_df, incomplete_df


def main():
    retrain = ("--retrain-model" in sys.argv)
    build_cache_pipeline(retrain_production_model=retrain)


if __name__ == "__main__":
    main()
