"""
Phase 3C: 市場オッズ基準モデルおよびAI統合モジュール
- 単勝オッズからの市場確率近似（q_i = 1/odds_i, p_market = q_i / sum(q_j)）
- 過去データ（OOF）に基づくAI予測と市場確率の最適ブレンド重み決定（リークなし）
- オッズ帯別の市場確率とAI予測の校正・比較評価
"""
import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple, List, Optional
try:
    from sklearn.metrics import log_loss, roc_auc_score, brier_score_loss
except ImportError:
    log_loss = None
    roc_auc_score = None
    brier_score_loss = None


def compute_market_probabilities_array(odds_arr: np.ndarray, active_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """
    1レース分の単勝オッズ配列から市場確率（控除率を考慮した正規化確率）を算出する。
    q_i = 1 / odds_i
    p_market_i = q_i / sum(q_j) (有効出走馬のみ)
    """
    odds = np.asarray(odds_arr, dtype=float)
    p_market = np.zeros_like(odds)

    if active_mask is None:
        active_mask = (odds >= 1.0) & np.isfinite(odds)
    else:
        active_mask = np.asarray(active_mask, dtype=bool) & (odds >= 1.0) & np.isfinite(odds)

    if not active_mask.any():
        return p_market

    # q_i = 1 / odds_i
    q = np.zeros_like(odds)
    q[active_mask] = 1.0 / odds[active_mask]
    sum_q = q[active_mask].sum()

    if sum_q > 0:
        p_market[active_mask] = q[active_mask] / sum_q
    else:
        p_market[active_mask] = 1.0 / active_mask.sum()

    return p_market


def compute_market_probabilities(
    df: pd.DataFrame,
    odds_col: str = "odds",
    race_id_col: str = "race_id",
    out_col: str = "p_market",
    *,
    active_col: Optional[str] = None,
    strict: bool = False,
) -> pd.DataFrame:
    """Estimate market probabilities using ONLY odds and pre-race runner eligibility.

    Never infer runner eligibility from finish_order, time_str, time_sec or
    any other post-race field. An explicit ``active_col`` must represent a
    status known at the prediction cutoff (e.g. pre-race scratch notices).
    When no eligibility feed exists, valid quoted odds are treated as active;
    this is an approximation and must not be described as verified starters.

    ``strict=True`` rejects a race with missing/invalid active-runner odds,
    instead of silently renormalizing the remaining runners.
    """
    res_df = df.copy()
    odds = pd.to_numeric(res_df[odds_col], errors="coerce")
    quoted = odds.ge(1.0) & np.isfinite(odds.to_numpy(dtype=float))

    if active_col is not None:
        if active_col not in res_df.columns:
            raise KeyError(f"Missing pre-race active status column: {active_col}")
        active_values = res_df[active_col]
        if active_values.isna().any() or not active_values.isin([True, False]).all():
            raise ValueError("Pre-race runner status must be explicit booleans")
        eligible = active_values.astype(bool)
    else:
        eligible = pd.Series(True, index=res_df.index)

    if strict and (eligible & ~quoted).any():
        bad_races = res_df.loc[eligible & ~quoted, race_id_col].unique().tolist()
        raise ValueError(f"Active runners have missing/invalid odds: {bad_races[:10]}")

    valid = eligible & quoted
    res_df["_inv_odds"] = np.where(valid, 1.0 / odds.where(valid, 1.0), 0.0)
    sums = res_df.groupby(race_id_col)["_inv_odds"].transform("sum")
    if strict and (sums <= 0).any():
        raise ValueError("Race without valid active-runner odds")
    res_df[out_col] = np.where(sums > 0, res_df["_inv_odds"] / sums.where(sums > 0, 1.0), 0.0)
    return res_df.drop(columns=["_inv_odds"])


def blend_probabilities(
    df: pd.DataFrame,
    p_ai_col: str = "p_win_ai",
    p_market_col: str = "p_market",
    weight_ai: float = 0.5,
    race_id_col: str = "race_id",
    out_col: str = "p_blend"
) -> pd.DataFrame:
    """
    AI予測確率と市場確率を重み付き線形結合する。
    p_blend = w * p_ai + (1 - w) * p_market
    結合後、レース単位の合計を1.0に再正規化する。
    """
    res_df = df.copy()
    w = float(np.clip(weight_ai, 0.0, 1.0))
    p_ai = res_df[p_ai_col].fillna(0.0).values
    p_m = res_df[p_market_col].fillna(0.0).values

    raw_blend = w * p_ai + (1.0 - w) * p_m
    res_df[out_col] = raw_blend

    # レース単位の合計1.0再正規化
    sums = res_df.groupby(race_id_col)[out_col].transform("sum").values
    res_df[out_col] = np.where(sums > 0, res_df[out_col] / sums, res_df[out_col])

    return res_df


def find_optimal_blend_weight(
    y_true: np.ndarray,
    p_ai: np.ndarray,
    p_market: np.ndarray,
    metric: str = "log_loss"
) -> float:
    """
    過去データ（OOF予測値）のみを用いて、Log Loss を最小化する最適なAI結合重み w* を探索する。
    探索グリッド: w in [0.00, 0.01, ..., 1.00]
    """
    if log_loss is None:
        raise ImportError("scikit-learn is required for find_optimal_blend_weight")
    y_true = np.asarray(y_true, dtype=int)
    p_ai = np.asarray(p_ai, dtype=float).clip(1e-7, 1.0 - 1e-7)
    p_m = np.asarray(p_market, dtype=float).clip(1e-7, 1.0 - 1e-7)

    best_w = 0.5
    best_score = float("inf")

    weights = np.linspace(0.0, 1.0, 101)
    for w in weights:
        blend = w * p_ai + (1.0 - w) * p_m
        blend = np.clip(blend, 1e-7, 1.0 - 1e-7)
        if metric == "log_loss":
            score = log_loss(y_true, blend)
        elif metric == "brier":
            score = brier_score_loss(y_true, blend)
        else:
            score = -roc_auc_score(y_true, blend)

        if score < best_score:
            best_score = score
            best_w = float(w)

    return round(best_w, 2)
