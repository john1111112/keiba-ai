import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional


def calculate_kelly_bet(win_prob: float, odds: float, bankroll: int = 10000, fraction: float = 0.25) -> Dict[str, Any]:
    """
    ケリー基準（クォーターケリー）による賭け金計算（1着確率モデル完成後に使用）
    """
    if odds <= 1.0 or win_prob <= 0.0:
        return {"bet_amount": 0, "kelly_fraction": 0.0, "is_bet": False}

    b = odds - 1.0
    p = min(0.85, max(0.01, win_prob))
    q = 1.0 - p

    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return {"bet_amount": 0, "kelly_fraction": 0.0, "is_bet": False}

    adj_kelly = min(0.15, full_kelly * fraction)
    raw_amount = bankroll * adj_kelly
    bet_amount = int(round(raw_amount / 100.0) * 100)
    bet_amount = max(300, min(bet_amount, 2000))

    return {
        "bet_amount": bet_amount,
        "kelly_fraction": round(adj_kelly * 100, 2),
        "is_bet": True
    }


def evaluate_race_value(pred_race: pd.DataFrame, min_ev_threshold: float = 1.0) -> Dict[str, Any]:
    """
    【Phase 3A: 期待値誤計算の廃止・旧ルール参考評価の明示・穴馬購入推奨の一時停止】
    1. 3着以内確率(pred_score) × 単勝オッズを単勝期待値として扱うこと、および 3着以内確率を単勝勝率（または1/2.6等の仮定式）として扱うことを完全廃止。
    2. 単勝期待値は「1着確率モデル」が完成するまで算出不可（None/NaN）とする。
    3. 複勝期待値は「JRA公式の複勝オッズ」が取得できるまで算出不可（None/NaN）とする。
    4. 現在の SS・S・A・B ランクは「旧ルール（3着以内予測確率と単勝オッズ帯）による参考評価」であることを明示する。
    5. 期待値を根拠とした穴馬判定（top_ana）および購入推奨（should_bet / recommendations）は一時停止する。
    """
    if len(pred_race) == 0:
        return {
            "rank": "見送り",
            "reason": "出走データなし",
            "should_bet": False,
            "bet_style": "none",
            "is_reference_rank": True,
            "win_ev_status": "算出不可（1着確率モデル未構築）",
            "place_ev_status": "算出不可（実複勝オッズ未取得）",
            "recommendations": []
        }

    top1 = pred_race.iloc[0]
    score1 = float(top1["pred_score"])
    odds1 = float(top1["odds"]) if pd.notna(top1["odds"]) else 0.0

    # オッズ未発表（JRA発売前）のレース
    if odds1 <= 0.0:
        return {
            "rank": "⏳【オッズ未確定】JRA発売前",
            "reason": "JRAの馬券発売開始前のためオッズが未発表です。過去走データからの3着以内予測確率順位のみ参考表示しています。",
            "should_bet": False,
            "bet_style": "none",
            "is_reference_rank": True,
            "win_ev_status": "算出不可（オッズ未発表・1着確率モデル未構築）",
            "place_ev_status": "算出不可（オッズ未発表・実複勝オッズ未取得）",
            "fukusho_est_odds": None,
            "honmei": {
                "horse_number": int(top1["horse_number"]),
                "horse_name": top1["horse_name"],
                "pred_score": round(score1 * 100, 1),
                "odds": 0.0,
                "fukusho_odds": None,
                "expected_value": None,
                "win_expected_value": None,
                "place_expected_value": None,
                "kelly_amount": 0
            },
            "top_ana": None,
            "plunge_horse": None,
            "recommendations": []
        }

    # 旧ルールによる参考ランク区分（3着以内予測確率と単勝オッズ帯のみによる参考分類）
    is_ss = (score1 >= 0.30 and 15.0 <= odds1 <= 30.0)
    is_s = (not is_ss) and (score1 >= 0.52 and odds1 < 15.0)
    is_a = (not is_ss) and (not is_s) and (0.42 <= score1 < 0.52 and odds1 <= 25.0)
    is_b = (not is_ss) and (not is_s) and (not is_a) and (score1 >= 0.34)

    note_suffix = "（※現在は旧ルールによる参考評価です。単勝期待値は1着確率モデル完成まで、複勝期待値は実複勝オッズ取得まで算出不可のため、期待値を根拠とした購入推奨は一時停止中です）"

    if is_ss:
        rank = "🔥【SSランク（旧ルール参考評価）】"
        reason = f"本命◎の3着以内予測確率 {score1*100:.1f}% / 単勝 {odds1:.1f}倍。{note_suffix}"
    elif is_s:
        rank = "⚡【Sランク（旧ルール参考評価）】"
        reason = f"本命◎の3着以内予測確率 {score1*100:.1f}% / 単勝 {odds1:.1f}倍。{note_suffix}"
    elif is_a:
        rank = "⭐【Aランク（旧ルール参考評価）】"
        reason = f"本命◎の3着以内予測確率 {score1*100:.1f}% / 単勝 {odds1:.1f}倍。{note_suffix}"
    elif is_b:
        rank = "【Bランク（旧ルール参考評価）】"
        reason = f"本命◎の3着以内予測確率 {score1*100:.1f}% / 単勝 {odds1:.1f}倍。{note_suffix}"
    else:
        rank = "⚠️【見送り（旧ルール参考評価）】"
        reason = f"本命◎の3着以内予測確率 {score1*100:.1f}%。{note_suffix}"

    return {
        "rank": rank,
        "reason": reason,
        "should_bet": False,  # 期待値を根拠とした購入推奨は一時停止
        "bet_style": "none",
        "is_reference_rank": True,
        "win_ev_status": "算出不可（1着確率モデル未構築）",
        "place_ev_status": "算出不可（実複勝オッズ未取得）",
        "fukusho_est_odds": None,
        "honmei": {
            "horse_number": int(top1["horse_number"]),
            "horse_name": top1["horse_name"],
            "pred_score": round(score1 * 100, 1),
            "odds": odds1,
            "fukusho_odds": None,
            "expected_value": None,
            "win_expected_value": None,
            "place_expected_value": None,
            "kelly_amount": 0
        },
        "top_ana": None,  # 期待値を根拠とした穴馬判定は一時停止
        "plunge_horse": None,
        "recommendations": []
    }
