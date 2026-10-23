import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional

def calculate_kelly_bet(win_prob: float, odds: float, bankroll: int = 10000, fraction: float = 0.25) -> Dict[str, Any]:
    """
    ケリー基準（分数ケリー: 破綻防止のためクォーターケリー採用）による最適賭け金計算
    f* = (b * p - q) / b
    b = オッズ - 1, p = 勝率, q = 1 - p
    """
    if odds <= 1.0 or win_prob <= 0.0:
        return {"bet_amount": 0, "kelly_fraction": 0.0, "is_bet": False}
        
    b = odds - 1.0
    p = win_prob
    q = 1.0 - p
    
    # 完全ケリー比率
    full_kelly = (b * p - q) / b
    
    if full_kelly <= 0:
        # 期待値がマイナス（買わない方が良い）
        return {"bet_amount": 0, "kelly_fraction": 0.0, "is_bet": False}
        
    # クォーターケリー（安全に資産を最大化）
    adj_kelly = min(0.15, full_kelly * fraction)  # 1レース最大15%上限
    raw_amount = bankroll * adj_kelly
    
    # 100円単位に丸め（最低100円）
    bet_amount = int(round(raw_amount / 100.0) * 100)
    bet_amount = max(100, min(bet_amount, 2000))
    
    return {
        "bet_amount": bet_amount,
        "kelly_fraction": round(adj_kelly * 100, 2),
        "is_bet": True
    }

def evaluate_race_value(pred_race: pd.DataFrame, min_ev_threshold: float = 1.25) -> Dict[str, Any]:
    """
    レース全体の勝負度判定（Sランク、Aランク、Bランク、見送り）と最適買い目・資金配分の決定
    """
    if len(pred_race) == 0:
        return {"rank": "見送り", "reason": "出走データなし", "should_bet": False}
        
    top1 = pred_race.iloc[0]
    top2 = pred_race.iloc[1] if len(pred_race) > 1 else None
    top3 = pred_race.iloc[2] if len(pred_race) > 2 else None
    
    # 穴馬（期待値1.6以上かつオッズ7倍以上）
    ana_horses = pred_race[pred_race["is_ana"]]
    top_ana = ana_horses.iloc[0] if len(ana_horses) > 0 else None
    
    score1 = top1["pred_score"]
    odds1 = top1["odds"]
    ev1 = top1["expected_value"]
    
    # 買い目候補における最大リターン（想定オッズ倍率）を算出
    max_return_odds = odds1
    if top_ana is not None and top_ana.get("odds", 0) > max_return_odds:
        max_return_odds = top_ana["odds"]
    if top2 is not None:
        est_umaren_odds = odds1 * top2["odds"] * 0.55
        if est_umaren_odds > max_return_odds:
            max_return_odds = est_umaren_odds

    # SSランク: Sランク基準（好走率70%以上 & 期待値1.3以上）かつ「15倍以上のリターンが見込める買い目・妙味馬」が存在
    # または 単勝オッズそのものが15倍以上かつAI好走率が突出している高配当ロマン勝負
    if (score1 >= 0.70 and ev1 >= 1.3 and max_return_odds >= 15.0) or (score1 >= 0.50 and odds1 >= 15.0 and ev1 >= 2.0):
        rank = "🔥【SSランク】超ド級・高配当勝負レース"
        reason = f"AI好走率{score1*100:.1f}%＆期待値{ev1:.2f}倍！さらに【想定15倍以上（最高{max_return_odds:.1f}倍）】の高リターンが見込める特大チャンス！"
        should_bet = True
    # Sランク: 好走率70%以上 かつ 期待値1.3以上（超激アツ）
    elif score1 >= 0.70 and ev1 >= 1.3:
        rank = "Sランク (超激アツ勝負レース)"
        reason = "AI好走率70%超え ＆ オッズ妙味十分。高額勝負推奨！"
        should_bet = True
    # Aランク: 好走率60%以上 かつ 期待値1.25以上
    elif score1 >= 0.60 and ev1 >= 1.25:
        rank = "Aランク (自信あり本命レース)"
        reason = "AI本命の信頼度高く、オッズ期待値もプラス域。"
        should_bet = True
    # 穴狙いAランク: 期待値1.8以上の穴馬が存在
    elif top_ana is not None and top_ana["expected_value"] >= 1.8:
        rank = "Aランク (穴馬妙味レース)"
        reason = f"推奨穴馬({top_ana['horse_number']}番 {top_ana['horse_name']})の期待値が{top_ana['expected_value']:.2f}倍と極めて高い。"
        should_bet = True
    # Bランク: 期待値1.15以上
    elif ev1 >= 1.15 or (top2 is not None and top2["expected_value"] >= 1.3):
        rank = "Bランク (少額勝負)"
        reason = "期待値はプラスだが混戦模様。少額での購入推奨。"
        should_bet = True
    else:
        rank = "⚠️ 見送り（ケン推奨）"
        reason = "期待値が低く控除率に負けるリスクが高いレース。見送りがベスト！"
        should_bet = False
        
    # 資金配分の算出
    k_res = calculate_kelly_bet(win_prob=score1, odds=odds1)
    
    # 推奨買い目プラン
    recommendations = []
    if should_bet:
        # プラン1: 本命単勝（ケリー基準配分）
        recommendations.append({
            "ticket_type": "単勝",
            "combination": f"{top1['horse_number']}番 ({top1['horse_name']})",
            "amount": k_res["bet_amount"] if rank.startswith("S") or rank.startswith("A") else 100,
            "target_odds": odds1,
            "expected_value": round(ev1, 2)
        })
        
        # プラン2: 穴馬が存在する場合はワイド流し
        if top_ana is not None and top_ana["horse_number"] != top1["horse_number"]:
            recommendations.append({
                "ticket_type": "ワイド (高配当狙い)",
                "combination": f"{top1['horse_number']} - {top_ana['horse_number']}",
                "amount": 300 if rank.startswith("S") else 200,
                "target_odds": f"想定{top_ana['odds']}倍絡み",
                "expected_value": round(top_ana["expected_value"], 2)
            })
            
        # プラン3: 馬連（対抗○へ流し）
        if top2 is not None:
            recommendations.append({
                "ticket_type": "馬連 (本線)",
                "combination": f"{top1['horse_number']} - {top2['horse_number']}",
                "amount": 300 if rank.startswith("S") or "SS" in rank else 100,
                "target_odds": f"想定{(odds1 * top2['odds'] * 0.55):.1f}倍",
                "expected_value": round((ev1 + top2['expected_value']) / 2, 2)
            })

        # プラン4: 3連複フォーメーション (◎ ➜ ○▲ ➜ ○▲☆ 計3〜5点)
        if top2 is not None and top3 is not None:
            ana_num = top_ana['horse_number'] if top_ana is not None else "-"
            sanrenpuku_combo_str = f"1頭目:{top1['horse_number']} ➜ 2頭目:{top2['horse_number']},{top3['horse_number']} ➜ 3頭目:{top2['horse_number']},{top3['horse_number']},{ana_num}"
            recommendations.append({
                "ticket_type": "3連複 (フォーメーション)",
                "combination": sanrenpuku_combo_str,
                "amount": 500 if "SS" in rank else (300 if "Sランク" in rank else 100),
                "target_odds": "想定18〜65倍",
                "expected_value": round((ev1 * 1.4), 2)
            })

        # プラン5: 3連単（特大万馬券狙い・SSランク/Sランク限定）
        if ("SSランク" in rank or "Sランク" in rank) and top2 is not None and top3 is not None:
            sanrentan_combo_str = f"1着:◎({top1['horse_number']}) ➜ 2着:○▲({top2['horse_number']},{top3['horse_number']}) ➜ 3着:○▲☆"
            recommendations.append({
                "ticket_type": "3連単 (特大万馬券狙い)",
                "combination": sanrentan_combo_str,
                "amount": 200 if "SS" in rank else 100,
                "target_odds": "想定80〜350倍超",
                "expected_value": round((ev1 * 2.2), 2)
            })
            
    return {
        "rank": rank,
        "reason": reason,
        "should_bet": should_bet,
        "honmei": {
            "horse_number": top1["horse_number"],
            "horse_name": top1["horse_name"],
            "pred_score": round(score1 * 100, 1),
            "odds": odds1,
            "expected_value": round(ev1, 2),
            "kelly_amount": k_res["bet_amount"]
        },
        "top_ana": {
            "horse_number": top_ana["horse_number"],
            "horse_name": top_ana["horse_name"],
            "odds": top_ana["odds"],
            "expected_value": round(top_ana["expected_value"], 2)
        } if top_ana is not None else None,
        "recommendations": recommendations
    }
