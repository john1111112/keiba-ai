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
            # Platt Scaling: ロジスティック回帰
            log_odds = np.log(y_prob / (1.0 - y_prob)).reshape(-1, 1)
            clf = LogisticRegression(solver="lbfgs", C=1.0)
            clf.fit(log_odds, y_true)
            self.calibrator = clf
            self.is_fitted = True
            return self

        elif self.method == "isotonic":
            # Isotonic Regression: 単調増加境界回帰
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

    # 確率帯別ビン集計
    if bin_edges is None:
        bin_edges = [0.0, 0.03, 0.06, 0.10, 0.15, 0.20, 0.30, 0.40, 0.60, 1.0]

    binned_df = pd.DataFrame({"y_true": y_true, "y_prob": y_prob})
    binned_df["bin"] = pd.cut(binned_df["y_prob"], bins=bin_edges, include_lowest=True)

    bin_stats = []
    for interval, group in binned_df.groupby("bin", observed=False):
        cnt = len(group)
        if cnt > 0:
            avg_pred = float(group["y_prob"].mean())
            act_win = float(group["y_true"].mean())
            bin_stats.append({
                "range": str(interval),
                "count": int(cnt),
                "mean_pred": round(avg_pred, 4),
                "actual_win_rate": round(act_win, 4),
                "diff": round(avg_pred - act_win, 4),
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
        "calibration_curve": {
            "prob_pred": [round(float(p), 4) for p in prob_pred],
            "prob_true": [round(float(p), 4) for p in prob_true],
        },
        "bin_stats": bin_stats,
    }


def enforce_probability_consistency(
    race_df: pd.DataFrame,
    win_prob_col: str = "pred_win_prob",
    place_prob_col: Optional[str] = "pred_place_prob",
    normalize_to_one: bool = True
) -> pd.DataFrame:
    """
    【確率の整合性強制関数】
    1. 予測値が [0, 1] の範囲内にあることを保証。
    2. 1着確率が3着以内確率を超えない (P(Win) <= P(Top3))。
    3. 各レースの1着確率の合計が 1.0 になるよう完走馬間で正規化。
    4. 取消馬・除外馬（finish_order not in 1..28 または time_str in ('', '-')）は正規化から除外し P(Win) = 0 とする。
    """
    df = race_df.copy()

    # 1. 範囲クリップ
    df[win_prob_col] = df[win_prob_col].fillna(0.0).clip(0.0, 1.0)

    # 2. 3着内確率との上限バウンド
    if place_prob_col and place_prob_col in df.columns:
        df[place_prob_col] = df[place_prob_col].fillna(0.0).clip(0.0, 1.0)
        df[win_prob_col] = np.minimum(df[win_prob_col], df[place_prob_col])

    # 3. 有効完走馬判定（取消・除外・競走中止は確率0）
    if "time_str" in df.columns:
        valid_mask = (df["finish_order"].fillna(0) >= 0) & (~df["time_str"].fillna("").astype(str).str.strip().isin(["", "-"]))
    else:
        valid_mask = (df["finish_order"].fillna(0) >= 0)

    # 未確定レース（finish_order == 0）は全員出走予定として扱う
    unconfirmed = (df["finish_order"].fillna(0) == 0)
    effective_runner_mask = valid_mask | unconfirmed

    # 取消馬等は 0.0
    df.loc[~effective_runner_mask, win_prob_col] = 0.0

    # 4. レース単位の合計1.0正規化（ベクトル化処理）
    if normalize_to_one and "race_id" in df.columns:
        runners = df.loc[effective_runner_mask]
        sums = runners.groupby("race_id")[win_prob_col].transform("sum")
        valid_div = sums > 0
        df.loc[runners.index[valid_div], win_prob_col] = runners.loc[valid_div, win_prob_col] / sums[valid_div]

    return df
