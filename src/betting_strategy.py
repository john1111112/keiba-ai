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
    レース全体の勝負度判定（SSランク、Sランク、Aランク、Bランク、見送り）と最適買い目・資金配分の決定
    """
    if len(pred_race) == 0:
        return {"rank": "見送り", "reason": "出走データなし", "should_bet": False}
        
    top1 = pred_race.iloc[0]
    top2 = pred_race.iloc[1] if len(pred_race) > 1 else None
    top3 = pred_race.iloc[2] if len(pred_race) > 2 else None
    
    # 穴馬（期待値1.5以上かつオッズ8倍以上）
    ana_horses = pred_race[pred_race["is_ana"]] if "is_ana" in pred_race.columns else pd.DataFrame()
    top_ana = ana_horses.iloc[0] if len(ana_horses) > 0 else None
    
    score1 = float(top1["pred_score"])
    odds1 = float(top1["odds"])
    ev1 = float(top1["expected_value"])
    
    # 厳格なランク認定ルール:
    # 1. SSランク: Sランク基準の中でもAI好走率85%以上 かつ 単勝15倍〜30倍の15倍以上高配当が見込める超厳選レース
    is_ss = (score1 >= 0.85 and 15.0 <= odds1 <= 30.0 and ev1 >= 1.25)
    # 2. Sランク: AI好走率73%以上 かつ 単勝15倍未満の手堅い超高勝率・絶対本命レース
    is_s = (not is_ss) and (score1 >= 0.73 and odds1 < 15.0 and ev1 >= 1.25)
    # 3. Aランク: AI好走率62%以上〜73%未満 かつ 期待値1.20以上（単勝25倍以下）の自信あり本命レース
    is_a = (not is_ss) and (not is_s) and (0.62 <= score1 < 0.73 and odds1 <= 25.0 and ev1 >= 1.20)
    
    if is_ss:
        rank = "🔥【SSランク】高勝率×15倍以上特大配当レース"
        reason = f"AI好走率{score1*100:.1f}%のSランク最上位基準でありながら、単勝{odds1:.1f}倍（15倍以上）の特大リターンが見込める厳選お宝レース！"
        should_bet = True
    elif is_s:
        rank = "⚡【Sランク】超高勝率・絶対本命レース"
        reason = f"AI好走率{score1*100:.1f}%！手堅く高確率で1着（単勝）を狙える絶対本命勝負レース。"
        should_bet = True
    elif is_a:
        rank = "⭐【Aランク】自信あり本命レース"
        reason = f"AI好走率{score1*100:.1f}%。十分な勝率と期待値が見込める好条件レース。"
        should_bet = True
    elif ev1 >= 1.15:
        rank = "【Bランク】標準・少額勝負"
        reason = "混戦模様のため少額推奨または見送り推奨。"
        should_bet = True
    else:
        rank = "⚠️ 見送り（ケン推奨）"
        reason = "混戦でリスクが高いため見送りがベスト！"
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
            "amount": k_res["bet_amount"] if ("Sランク" in rank or "Aランク" in rank) else 100,
            "target_odds": odds1,
            "expected_value": round(ev1, 2)
        })
        
        # プラン2: 穴馬が存在する場合はワイド流し
        if top_ana is not None and top_ana["horse_number"] != top1["horse_number"]:
            recommendations.append({
                "ticket_type": "ワイド (高配当狙い)",
                "combination": f"{top1['horse_number']} - {top_ana['horse_number']}",
                "amount": 300 if "Sランク" in rank else 200,
                "target_odds": f"想定{top_ana['odds']}倍絡み",
                "expected_value": round(float(top_ana["expected_value"]), 2)
            })
            
        # プラン3: 馬連（対抗○へ流し）
        if top2 is not None:
            recommendations.append({
                "ticket_type": "馬連 (本線)",
                "combination": f"{top1['horse_number']} - {top2['horse_number']}",
                "amount": 300 if "Sランク" in rank else 100,
                "target_odds": f"想定{(odds1 * float(top2['odds']) * 0.55):.1f}倍",
                "expected_value": round((ev1 + float(top2['expected_value'])) / 2, 2)
            })

        # プラン4: 3連複フォーメーション (◎ ➜ ○▲ ➜ ○▲☆ 計3〜5点)
        if top2 is not None and top3 is not None:
            ana_num = top_ana['horse_number'] if top_ana is not None else "-"
            sanrenpuku_combo_str = f"1頭目:{top1['horse_number']} ➜ 2頭目:{top2['horse_number']},{top3['horse_number']} ➜ 3頭目:{top2['horse_number']},{top3['horse_number']},{ana_num}"
            recommendations.append({
                "ticket_type": "3連複 (フォーメーション)",
                "combination": sanrenpuku_combo_str,
                "amount": 500 if "SSランク" in rank else (300 if "Sランク" in rank else 100),
                "target_odds": "想定18〜65倍",
                "expected_value": round((ev1 * 1.4), 2)
            })

        # プラン5: 3連単（特大万馬券狙い・SSランク/Sランク限定）
        if "Sランク" in rank and top2 is not None and top3 is not None:
            sanrentan_combo_str = f"1着:◎({top1['horse_number']}) ➜ 2着:○▲({top2['horse_number']},{top3['horse_number']}) ➜ 3着:○▲☆"
            recommendations.append({
                "ticket_type": "3連単 (特大万馬券狙い)",
                "combination": sanrentan_combo_str,
                "amount": 200 if "SSランク" in rank else 100,
                "target_odds": "想定80〜350倍超",
                "expected_value": round((ev1 * 2.2), 2)
            })
            
    plunge_df = pred_race[(pred_race["expected_value"] >= 1.3) & (pred_race["odds"] <= 8.5) & (pred_race["pred_score"] >= 0.55)]
    target_plunge = plunge_df.iloc[0] if len(plunge_df) > 0 else None

    return {
        "rank": rank,
        "reason": reason,
        "should_bet": should_bet,
        "honmei": {
            "horse_number": int(top1["horse_number"]),
            "horse_name": top1["horse_name"],
            "pred_score": round(score1 * 100, 1),
            "odds": odds1,
            "expected_value": round(ev1, 2),
            "kelly_amount": k_res["bet_amount"]
        },
        "top_ana": {
            "horse_number": int(top_ana["horse_number"]),
            "horse_name": top_ana["horse_name"],
            "odds": float(top_ana["odds"]),
            "expected_value": round(float(top_ana["expected_value"]), 2)
        } if top_ana is not None else None,
        "plunge_horse": {
            "horse_number": int(target_plunge["horse_number"]),
            "horse_name": target_plunge["horse_name"],
            "odds": float(target_plunge["odds"]),
            "expected_value": round(float(target_plunge["expected_value"]), 2)
        } if target_plunge is not None else None,
        "recommendations": recommendations
    }
