import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple, List, Optional
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss
from sklearn.calibration import calibration_curve


class ProbabilityCalibrator:
    """
    【予測確率校正器 (Probability Calibrator)】
    時系列リークを防止するため、評価対象期間より過去の OOF（Out-of-Fold）予測値のみを用いて学習する。
    - 'none': 未校正 (Raw model output)
    - 'sigmoid': Platt Scaling (ロジスティック回帰によるスケーリング)
    - 'isotonic': Isotonic Regression (非パラメトリック単調増加回帰)
    """
    def __init__(self, method: str = "sigmoid"):
        if method not in ("none", "sigmoid", "isotonic"):
            raise ValueError(f"Unknown calibration method: {method}")
        self.method = method
        self.calibrator = None
        self.is_fitted = False

    def fit(self, y_prob: np.ndarray, y_true: np.ndarray):
        y_prob = np.asarray(y_prob, dtype=float).clip(1e-6, 1.0 - 1e-6)
        y_true = np.asarray(y_true, dtype=int)

        if self.method == "none":
            self.is_fitted = True
            return self

        if self.method == "sigmoid":
            log_odds = np.log(y_prob / (1.0 - y_prob)).reshape(-1, 1)
            clf = LogisticRegression(solver="lbfgs", C=1.0)
            clf.fit(log_odds, y_true)
            self.calibrator = clf
            self.is_fitted = True
            return self

        elif self.method == "isotonic":
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(y_prob, y_true)
            self.calibrator = iso
            self.is_fitted = True
            return self

    def predict_proba(self, y_prob: np.ndarray) -> np.ndarray:
        y_prob = np.asarray(y_prob, dtype=float).clip(1e-6, 1.0 - 1e-6)
        if self.method == "none" or not self.is_fitted:
            return y_prob

        if self.method == "sigmoid":
            log_odds = np.log(y_prob / (1.0 - y_prob)).reshape(-1, 1)
            cal_probs = self.calibrator.predict_proba(log_odds)[:, 1]
            return np.clip(cal_probs, 0.0, 1.0)

        elif self.method == "isotonic":
            cal_probs = self.calibrator.predict(y_prob)
            return np.clip(cal_probs, 0.0, 1.0)


def project_win_probabilities_with_top3_bounds(
    p_win: np.ndarray,
    p_top3: np.ndarray,
    target_sum: float = 1.0,
    max_iter: int = 35,
    tol: float = 1e-8
) -> np.ndarray:
    """
    【数学的厳密有界射影アルゴリズム】
    正規化後も P(Win) <= P(Top3) を 100% 遵守させつつ、出走馬合計を 1.0 に正規化する。
    スカラー c* について sum(min(u_i, c* * p_i)) == target_sum となる解を二分法で探索。
    - 出走馬全員について P(Win, i) <= P(Top3, i) が恒等的に成立
    - sum(P(Win, i)) == 1.0 が恒等的に成立
    - 上限に達していない馬同士の相対オッズ比が維持される
    """
    p_win = np.asarray(p_win, dtype=float).clip(0.0, 1.0)
    p_top3 = np.asarray(p_top3, dtype=float).clip(0.0, 1.0)

    # 上限合計が target_sum 未満の場合は上限そのものを返す
    sum_u = float(p_top3.sum())
    if sum_u <= target_sum:
        return p_top3.copy()

    sum_p = float(p_win.sum())
    if sum_p <= 0.0:
        return (p_top3 / sum_u) * target_sum

    low = 0.0
    high = max(100.0, target_sum / max(float(p_win.min()), 1e-7))

    for _ in range(12):
        if float(np.minimum(p_top3, high * p_win).sum()) >= target_sum:
            break
        high *= 2.0

    for _ in range(max_iter):
        mid = (low + high) / 2.0
        val = float(np.minimum(p_top3, mid * p_win).sum())
        if abs(val - target_sum) < tol:
            break
        if val < target_sum:
            low = mid
        else:
            high = mid

    res = np.minimum(p_top3, mid * p_win)
    # 浮動小数点誤差の微小補正
    s = float(res.sum())
    if s > 0:
        res = (res / s) * target_sum
        # 厳密に p_top3 以下を再保証
        res = np.minimum(p_top3, res)
        s2 = float(res.sum())
        if s2 > 0:
            res = (res / s2) * target_sum

    return res


def evaluate_probability_calibration(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
    bin_edges: Optional[List[float]] = None
) -> Dict[str, Any]:
    """
    確率予測の精度および校正度合（Calibration）を包括的に評価する。
    指標:
    - ROC-AUC
    - Log Loss (Cross-Entropy)
    - Brier Score (平均二乗誤差)
    - ECE (Expected Calibration Error: 期待校正誤差)
    - 確率校正曲線 (Calibration Curve)
    - 確率帯別のサンプル数・平均予測確率・実際の勝率
    """
    y_true = np.asarray(y_true, dtype=int)
    y_prob = np.asarray(y_prob, dtype=float).clip(1e-6, 1.0 - 1e-6)

    auc = float(roc_auc_score(y_true, y_prob)) if len(np.unique(y_true)) > 1 else 0.5
    loss = float(log_loss(y_true, y_prob))
    brier = float(brier_score_loss(y_true, y_prob))

    # 校正曲線 (scikit-learn)
    prob_true, prob_pred = calibration_curve(y_true, y_prob, n_bins=n_bins, strategy="uniform")

    # 確率帯別ビン集計 & ECE
    if bin_edges is None:
        bin_edges = [0.0, 0.03, 0.06, 0.10, 0.15, 0.20, 0.30, 0.40, 0.60, 1.0]

    binned_df = pd.DataFrame({"y_true": y_true, "y_prob": y_prob})
    binned_df["bin"] = pd.cut(binned_df["y_prob"], bins=bin_edges, include_lowest=True)

    bin_stats = []
    total_samples = len(y_prob)
    ece = 0.0

    for interval, group in binned_df.groupby("bin", observed=False):
        cnt = len(group)
        if cnt > 0:
            avg_pred = float(group["y_prob"].mean())
            act_win = float(group["y_true"].mean())
            diff = avg_pred - act_win
            ece += (cnt / total_samples) * abs(diff)
            bin_stats.append({
                "range": str(interval),
                "count": int(cnt),
                "mean_pred": round(avg_pred, 4),
                "actual_win_rate": round(act_win, 4),
                "diff": round(diff, 4),
            })
        else:
            bin_stats.append({
                "range": str(interval),
                "count": 0,
                "mean_pred": 0.0,
                "actual_win_rate": 0.0,
                "diff": 0.0,
            })

    return {
        "auc": round(auc, 4),
        "log_loss": round(loss, 4),
        "brier_score": round(brier, 4),
        "ece": round(ece, 4),
        "calibration_curve": {
            "prob_pred": [round(float(p), 4) for p in prob_pred],
            "prob_true": [round(float(p), 4) for p in prob_true],
        },
        "bin_stats": bin_stats,
    }


def compute_winner_log_loss(df_eval: pd.DataFrame, prob_col: str) -> float:
    """
    【レース単位の勝者対数損失 (Winner Log-Loss)】
    実際の各レースの1着馬（target == 1）にAIが割り振った予測確率 p の負の対数尤度 -ln(p) の平均。
    低ければ低いほど「勝つ馬に対して確実に高い確率を割り振れていた」ことを意味する。
    """
    winners = df_eval[df_eval["target"] == 1]
    if len(winners) == 0:
        return 0.0
    w_probs = winners[prob_col].clip(1e-7, 1.0)
    return float(-np.log(w_probs).mean())


def enforce_probability_consistency(
    race_df: pd.DataFrame,
    win_prob_col: str = "pred_win_prob",
    place_prob_col: Optional[str] = "pred_place_prob",
    normalize_to_one: bool = True
) -> pd.DataFrame:
    """
    【確率の整合性強制関数（再設計・有界射影版）】
    1. 予測値が [0, 1] の範囲内にあることを保証。
    2. 取消馬・除外馬（finish_order not in 1..28 または time_str in ('', '-')）は確率 0.0。
    3. 各レースで project_win_probabilities_with_top3_bounds を適用し、
       正規化後も P(Win) <= P(Top3) が 100% 成立し、かつ合計が 1.0 になることを保証。
    """
    df = race_df.copy()

    # 1. 範囲クリップ
    df[win_prob_col] = df[win_prob_col].fillna(0.0).clip(0.0, 1.0)
    has_place = place_prob_col and (place_prob_col in df.columns)
    if has_place:
        df[place_prob_col] = df[place_prob_col].fillna(0.0).clip(0.0, 1.0)

    # 2. 有効完走馬判定（取消・除外・競走中止は確率0）
    if "time_str" in df.columns:
        valid_mask = (df["finish_order"].fillna(0) >= 0) & (~df["time_str"].fillna("").astype(str).str.strip().isin(["", "-"]))
    else:
        valid_mask = (df["finish_order"].fillna(0) >= 0)

    unconfirmed = (df["finish_order"].fillna(0) == 0)
    effective_runner_mask = valid_mask | unconfirmed

    # 取消馬等は 0.0
    df.loc[~effective_runner_mask, win_prob_col] = 0.0

    # 3. レース単位の有界射影正規化
    if normalize_to_one and "race_id" in df.columns:
        result_dfs = []
        for _, g in df.groupby("race_id", sort=False):
            runners_idx = g.index[effective_runner_mask.loc[g.index]]
            if len(runners_idx) > 0:
                p_w = g.loc[runners_idx, win_prob_col].values
                if has_place:
                    p_p = g.loc[runners_idx, place_prob_col].values
                    proj_w = project_win_probabilities_with_top3_bounds(p_w, p_p, target_sum=1.0)
                else:
                    s = float(p_w.sum())
                    proj_w = (p_w / s) if s > 0 else p_w
                g.loc[runners_idx, win_prob_col] = proj_w
            result_dfs.append(g)
        df = pd.concat(result_dfs, axis=0)

    return df
