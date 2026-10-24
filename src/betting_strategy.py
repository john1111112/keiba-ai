import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional

def calculate_kelly_bet(win_prob: float, odds: float, bankroll: int = 10000, fraction: float = 0.25) -> Dict[str, Any]:
    """
    ケリー基準（クォーターケリー）による最適賭け金計算
    """
    if odds <= 1.0 or win_prob <= 0.0:
        return {"bet_amount": 0, "kelly_fraction": 0.0, "is_bet": False}

    b = odds - 1.0
    p = min(0.85, max(0.01, win_prob))
    q = 1.0 - p

    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return {"bet_amount": 500, "kelly_fraction": 0.0, "is_bet": False}

    adj_kelly = min(0.15, full_kelly * fraction)
    raw_amount = bankroll * adj_kelly
    bet_amount = int(round(raw_amount / 100.0) * 100)
    bet_amount = max(300, min(bet_amount, 2000))

    return {
        "bet_amount": bet_amount,
        "kelly_fraction": round(adj_kelly * 100, 2),
        "is_bet": True
    }

def estimate_fukusho_odds(win_odds: float) -> float:
    """単勝オッズから複勝参考オッズを算出（未確定時は0.0）"""
    if win_odds <= 0.0:
        return 0.0
    return round(max(1.1, min(12.0, 1.1 + (win_odds - 1.0) * 0.20)), 1)

def evaluate_race_value(pred_race: pd.DataFrame, min_ev_threshold: float = 1.0) -> Dict[str, Any]:
    """
    【実確率・リークゼロ版】
    LightGBMが算出した実際の3着以内好走確率(pred_score)と実オッズに基づき、
    手作業のスコア改ざんを一切行わずにレースランクを判定する。
    また、オッズ未発表(odds <= 0)のレースは「オッズ未確定」としてランク付け・通知対象外とする。
    """
    if len(pred_race) == 0:
        return {"rank": "見送り", "reason": "出走データなし", "should_bet": False, "bet_style": "none"}

    top1 = pred_race.iloc[0]
    top2 = pred_race.iloc[1] if len(pred_race) > 1 else None
    top3 = pred_race.iloc[2] if len(pred_race) > 2 else None

    score1 = float(top1["pred_score"])
    odds1 = float(top1["odds"]) if pd.notna(top1["odds"]) else 0.0

    # オッズ未発表（日曜の前日発売対象外レース等）は作り物のオッズを使わず正直に「オッズ未確定」とする
    if odds1 <= 0.0:
        return {
            "rank": "⏳【オッズ未確定】JRA発売前",
            "reason": "JRAの馬券発売開始前のためオッズが未発表です。架空の推定オッズによるランク付けや通知は行いません（過去走データからのAI好走率順位のみ表示しています）。",
            "should_bet": False,
            "bet_style": "none",
            "fukusho_est_odds": 0.0,
            "honmei": {
                "horse_number": int(top1["horse_number"]),
                "horse_name": top1["horse_name"],
                "pred_score": round(score1 * 100, 1),
                "odds": 0.0,
                "fukusho_odds": 0.0,
                "expected_value": 0.0,
                "kelly_amount": 0
            },
            "top_ana": None,
            "plunge_horse": None,
            "recommendations": []
        }

    fuku_odds1 = estimate_fukusho_odds(odds1)
    # 期待値: 3着以内確率 × 複勝参考オッズ（または推定勝率 × 単勝オッズの大きい方）
    est_win_prob1 = score1 / 2.6
    ev1 = round(max(est_win_prob1 * odds1, score1 * fuku_odds1), 2)

    ana_horses = pred_race[(pred_race["odds"] >= 10.0) & (pred_race["odds"] <= 35.0) & (pred_race["pred_score"] >= 0.25)]
    top_ana = ana_horses.iloc[0] if len(ana_horses) > 0 else None

    is_fukusho_style = (odds1 >= 8.0)
    bet_style = "fukusho_wide" if is_fukusho_style else "tansho_umaren"

    # 実確率に基づくランク判定基準（前半期間で固定した閾値）:
    # 1. SSランク: 本命◎が単勝15〜30倍の穴馬でありながら、LightGBMの3着以内確率が30%以上（期待値大幅プラス）
    is_ss = (score1 >= 0.30 and 15.0 <= odds1 <= 30.0)
    # 2. Sランク: 本命◎が単勝15倍未満かつLightGBMの3着以内確率が52%以上
    is_s = (not is_ss) and (score1 >= 0.52 and odds1 < 15.0)
    # 3. Aランク: 本命◎が単勝25倍以下かつLightGBMの3着以内確率が42%〜52%未満
    is_a = (not is_ss) and (not is_s) and (0.42 <= score1 < 0.52 and odds1 <= 25.0)
    # 4. Bランク: 3着以内確率が34%〜42%未満
    is_b = (not is_ss) and (not is_s) and (not is_a) and (score1 >= 0.34)

    if is_ss:
        rank = "🔥【SSランク】高期待値×15倍以上穴本命レース"
        reason = f"単勝{odds1:.1f}倍の穴馬ながら、AIの3着以内予測確率が{score1*100:.1f}%と市場オッズ以上に高く評価されたレース。複勝・ワイドを中心に狙います。"
        should_bet = True
    elif is_s:
        rank = "⚡【Sランク】高確率・本命レース"
        reason = f"AIの3着以内予測確率{score1*100:.1f}%（単勝{odds1:.1f}倍）。過去走実績・騎手指標から好走確率が高いと予測される本命レース。"
        should_bet = True
    elif is_a:
        rank = "⭐【Aランク】好条件レース"
        reason = f"AIの3着以内予測確率{score1*100:.1f}%（単勝{odds1:.1f}倍）。上位争いが見込めるレース。"
        should_bet = True
    elif is_b:
        rank = "【Bランク】標準・混戦レース"
        reason = f"AIの3着以内予測確率{score1*100:.1f}%。実力が拮抗しているため少額または見送り推奨。"
        should_bet = True
    else:
        rank = "⚠️ 見送り（ケン推奨）"
        reason = f"AIの最大3着以内予測確率が{score1*100:.1f}%と低く、混戦のため見送り推奨。"
        should_bet = False

    k_res = calculate_kelly_bet(win_prob=est_win_prob1, odds=odds1)

    recommendations = []
    if should_bet:
        if is_fukusho_style:
            recommendations.append({
                "ticket_type": "複勝 (3着以内メイン推奨)",
                "combination": f"{top1['horse_number']}番 ({top1['horse_name']})",
                "amount": 700,
                "target_odds": f"参考{fuku_odds1}倍",
                "expected_value": round(score1 * fuku_odds1, 2)
            })
            if top2 is not None:
                recommendations.append({
                    "ticket_type": "ワイド (◎➜○)",
                    "combination": f"{top1['horse_number']} - {top2['horse_number']}",
                    "amount": 500,
                    "target_odds": "-",
                    "expected_value": ev1
                })
            recommendations.append({
                "ticket_type": "単勝 (1着少額)",
                "combination": f"{top1['horse_number']}番 ({top1['horse_name']})",
                "amount": 300,
                "target_odds": f"{odds1}倍",
                "expected_value": round(est_win_prob1 * odds1, 2)
            })
        else:
            recommendations.append({
                "ticket_type": "単勝 (1着メイン)",
                "combination": f"{top1['horse_number']}番 ({top1['horse_name']})",
                "amount": 700,
                "target_odds": f"{odds1}倍",
                "expected_value": round(est_win_prob1 * odds1, 2)
            })
            if top2 is not None:
                recommendations.append({
                    "ticket_type": "馬連 (◎➜○)",
                    "combination": f"{top1['horse_number']} - {top2['horse_number']}",
                    "amount": 500,
                    "target_odds": "-",
                    "expected_value": ev1
                })
            if top3 is not None:
                recommendations.append({
                    "ticket_type": "ワイド (◎➜▲)",
                    "combination": f"{top1['horse_number']} - {top3['horse_number']}",
                    "amount": 300,
                    "target_odds": "-",
                    "expected_value": ev1
                })

    return {
        "rank": rank,
        "reason": reason,
        "should_bet": should_bet,
        "bet_style": bet_style,
        "fukusho_est_odds": fuku_odds1,
        "honmei": {
            "horse_number": int(top1["horse_number"]),
            "horse_name": top1["horse_name"],
            "pred_score": round(score1 * 100, 1),
            "odds": odds1,
            "fukusho_odds": fuku_odds1,
            "expected_value": ev1,
            "kelly_amount": k_res["bet_amount"]
        },
        "top_ana": {
            "horse_number": int(top_ana["horse_number"]),
            "horse_name": top_ana["horse_name"],
            "odds": float(top_ana["odds"]),
            "expected_value": round(float(top_ana["pred_score"]) * estimate_fukusho_odds(float(top_ana["odds"])), 2)
        } if top_ana is not None else None,
        "plunge_horse": None,
        "recommendations": recommendations
    }
