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
    
    full_kelly = (b * p - q) / b
    
    if full_kelly <= 0:
        return {"bet_amount": 0, "kelly_fraction": 0.0, "is_bet": False}
        
    adj_kelly = min(0.15, full_kelly * fraction)
    raw_amount = bankroll * adj_kelly
    
    bet_amount = int(round(raw_amount / 100.0) * 100)
    bet_amount = max(100, min(bet_amount, 2000))
    
    return {
        "bet_amount": bet_amount,
        "kelly_fraction": round(adj_kelly * 100, 2),
        "is_bet": True
    }

def estimate_fukusho_odds(win_odds: float) -> float:
    """単勝オッズから現実的な複勝オッズ（3着以内払戻倍率）を算出"""
    return round(max(1.2, min(16.0, 1.1 + (win_odds - 1.0) * 0.22)), 1)

def evaluate_race_value(pred_race: pd.DataFrame, min_ev_threshold: float = 1.25) -> Dict[str, Any]:
    """
    レース全体の勝負度判定（SSランク、Sランク、Aランク、Bランク、見送り）と
    馬のオッズ特性に応じた最適買い方（単勝アタマ勝負 vs 複勝＆ワイド3着以内確保勝負）の自動決定
    """
    if len(pred_race) == 0:
        return {"rank": "見送り", "reason": "出走データなし", "should_bet": False, "bet_style": "none"}
        
    top1 = pred_race.iloc[0]
    top2 = pred_race.iloc[1] if len(pred_race) > 1 else None
    top3 = pred_race.iloc[2] if len(pred_race) > 2 else None
    
    ana_horses = pred_race[pred_race["is_ana"]] if "is_ana" in pred_race.columns else pd.DataFrame()
    top_ana = ana_horses.iloc[0] if len(ana_horses) > 0 else None
    
    score1 = float(top1["pred_score"])
    odds1 = float(top1["odds"])
    ev1 = float(top1["expected_value"])
    fuku_odds1 = estimate_fukusho_odds(odds1)
    
    # 買い方スタイルの自動判定:
    # 単勝8.0倍以上の中穴・大穴馬が本命(◎)の場合は、3着以内でも複勝・ワイドで高配当になるため
    # 無理な単勝メインではなく「複勝（3着以内メイン）＋ワイド・3連複プラン」を自動推奨する！
    is_fukusho_style = (odds1 >= 8.0)
    bet_style = "fukusho_wide" if is_fukusho_style else "tansho_umaren"
    
    # 厳格なランク認定ルール:
    # 1. SSランク: Sランク基準の中でもAI好走率85%以上 かつ 単勝15倍〜30倍の15倍以上高配当が見込める超厳選レース
    is_ss = (score1 >= 0.85 and 15.0 <= odds1 <= 30.0 and ev1 >= 1.25)
    # 2. Sランク: AI好走率73%以上 かつ 単勝15倍未満の手堅い超高勝率・絶対本命レース
    is_s = (not is_ss) and (score1 >= 0.73 and odds1 < 15.0 and ev1 >= 1.25)
    # 3. Aランク: AI好走率62%以上〜73%未満 かつ 期待値1.20以上（単勝25倍以下）の自信あり本命レース
    is_a = (not is_ss) and (not is_s) and (0.62 <= score1 < 0.73 and odds1 <= 25.0 and ev1 >= 1.20)
    
    if is_ss:
        rank = "🔥【SSランク】高勝率×15倍以上特大配当レース"
        reason = f"AI好走率{score1*100:.1f}%の超高確信度でありながら、単勝{odds1:.1f}倍（複勝想定{fuku_odds1}倍）の特大リターンが見込める厳選お宝レース！複勝・ワイドを厚めに張りつつ単勝・連系で特大配当を狙います。"
        should_bet = True
    elif is_s:
        rank = "⚡【Sランク】超高勝率・絶対本命レース"
        if is_fukusho_style:
            reason = f"AI好走率{score1*100:.1f}%・単勝{odds1:.1f}倍の妙味本命！3着以内確保の『複勝（想定{fuku_odds1}倍）＋ワイド流し』をメインに高配当を確実に仕留める勝負レース。"
        else:
            reason = f"AI好走率{score1*100:.1f}%！手堅く高確率で1着（単勝）および馬連本線を狙える絶対本命勝負レース。"
        should_bet = True
    elif is_a:
        rank = "⭐【Aランク】自信あり本命レース"
        if is_fukusho_style:
            reason = f"AI好走率{score1*100:.1f}%・単勝{odds1:.1f}倍の中穴本命！単勝ではなく3着以内で高配当がつく『複勝（想定{fuku_odds1}倍）＋ワイド・3連複』がベストな買い方です。"
        else:
            reason = f"AI好走率{score1*100:.1f}%。単勝・馬連を中心に十分な勝率と期待値が見込める好条件レース。"
        should_bet = True
    elif ev1 >= 1.15:
        rank = "【Bランク】穴軸・複勝＆ワイド少額勝負" if is_fukusho_style else "【Bランク】標準・少額勝負"
        if is_fukusho_style:
            reason = f"本命◎は単勝{odds1:.1f}倍の穴馬！単勝ではリスクが高いため、3着以内で高配当（想定{fuku_odds1}倍）になる『複勝メイン＋ワイド・3連複』で少額狙うのが最も賢い買い方です。"
        else:
            reason = "混戦模様のため少額推奨または見送り推奨。"
        should_bet = True
    else:
        rank = "⚠️ 見送り（ケン推奨）"
        reason = "混戦でリスクが高いため見送りがベスト！"
        should_bet = False
        
    k_res = calculate_kelly_bet(win_prob=score1, odds=odds1)
    
    recommendations = []
    if should_bet:
        if is_fukusho_style:
            # 【パターンB: 単勝8.0倍以上の妙味本命 ➜ 複勝（3着以内メイン）＋ワイド＋3連複プラン】
            fuku_amount = 1500 if ("SSランク" in rank or "Sランク" in rank) else (1000 if "Aランク" in rank else 800)
            fuku_ev = round(min(9.9, min(0.95, score1 * 1.35) * fuku_odds1), 2)
            recommendations.append({
                "ticket_type": "複勝 (3着以内メイン推奨)",
                "combination": f"{top1['horse_number']}番 ({top1['horse_name']}) [想定{fuku_odds1}倍]",
                "amount": fuku_amount,
                "target_odds": f"想定{fuku_odds1}倍",
                "expected_value": fuku_ev
            })
            
            if top2 is not None and top3 is not None:
                wide_combo = f"{top1['horse_number']} - {top2['horse_number']}, {top3['horse_number']} (本線2点)"
            elif top2 is not None:
                wide_combo = f"{top1['horse_number']} - {top2['horse_number']}"
            else:
                wide_combo = f"{top1['horse_number']}番流し"
            recommendations.append({
                "ticket_type": "ワイド (◎➜○▲ 本線)",
                "combination": wide_combo,
                "amount": 600 if ("SSランク" in rank or "Sランク" in rank) else 400,
                "target_odds": f"想定{max(3.5, round(fuku_odds1 * 2.2, 1))}倍",
                "expected_value": round(min(9.9, fuku_ev * 1.25), 2)
            })
            
            if top2 is not None and top3 is not None:
                ana_num = top_ana['horse_number'] if top_ana is not None else "-"
                sanrenpuku_combo_str = f"1頭目:{top1['horse_number']} ➜ 2頭目:{top2['horse_number']},{top3['horse_number']} ➜ 3頭目:{top2['horse_number']},{top3['horse_number']},{ana_num}"
                recommendations.append({
                    "ticket_type": "3連複 (フォーメーション)",
                    "combination": sanrenpuku_combo_str,
                    "amount": 500 if ("SSランク" in rank or "Sランク" in rank) else 300,
                    "target_odds": "想定25〜120倍",
                    "expected_value": round(min(9.9, fuku_ev * 1.5), 2)
                })
                
            recommendations.append({
                "ticket_type": "単勝 (1着ボーナス少額)",
                "combination": f"{top1['horse_number']}番 ({top1['horse_name']}) [{odds1}倍]",
                "amount": 300 if ("SSランク" in rank or "Sランク" in rank) else 200,
                "target_odds": odds1,
                "expected_value": round(min(9.9, ev1), 2)
            })
        else:
            # 【パターンA: 単勝8.0倍未満の堅実本命 ➜ 単勝（1着メイン）＋馬連＋3連複プラン】
            tan_amount = k_res["bet_amount"] if ("Sランク" in rank or "Aランク" in rank) else 500
            recommendations.append({
                "ticket_type": "単勝 (1着メイン推奨)",
                "combination": f"{top1['horse_number']}番 ({top1['horse_name']}) [{odds1}倍]",
                "amount": tan_amount,
                "target_odds": odds1,
                "expected_value": round(ev1, 2)
            })
            
            if top2 is not None:
                recommendations.append({
                    "ticket_type": "馬連 (◎➜○ 本線)",
                    "combination": f"{top1['horse_number']} - {top2['horse_number']}",
                    "amount": 500 if "Sランク" in rank else 300,
                    "target_odds": f"想定{(odds1 * float(top2['odds']) * 0.55):.1f}倍",
                    "expected_value": round((ev1 + float(top2['expected_value'])) / 2, 2)
                })
                
            if top_ana is not None and top_ana["horse_number"] != top1["horse_number"]:
                recommendations.append({
                    "ticket_type": "ワイド (穴馬☆流し)",
                    "combination": f"{top1['horse_number']} - {top_ana['horse_number']}",
                    "amount": 300 if "Sランク" in rank else 200,
                    "target_odds": f"想定{top_ana['odds']}倍絡み",
                    "expected_value": round(float(top_ana["expected_value"]), 2)
                })

            if top2 is not None and top3 is not None:
                ana_num = top_ana['horse_number'] if top_ana is not None else "-"
                sanrenpuku_combo_str = f"1頭目:{top1['horse_number']} ➜ 2頭目:{top2['horse_number']},{top3['horse_number']} ➜ 3頭目:{top2['horse_number']},{top3['horse_number']},{ana_num}"
                recommendations.append({
                    "ticket_type": "3連複 (フォーメーション)",
                    "combination": sanrenpuku_combo_str,
                    "amount": 400 if "Sランク" in rank else 200,
                    "target_odds": "想定12〜45倍",
                    "expected_value": round((ev1 * 1.3), 2)
                })

            if "Sランク" in rank and top2 is not None and top3 is not None:
                sanrentan_combo_str = f"1着:◎({top1['horse_number']}) ➜ 2着:○▲({top2['horse_number']},{top3['horse_number']}) ➜ 3着:○▲☆"
                recommendations.append({
                    "ticket_type": "3連単 (1着固定ボーナス)",
                    "combination": sanrentan_combo_str,
                    "amount": 200,
                    "target_odds": "想定40〜180倍",
                    "expected_value": round((ev1 * 1.8), 2)
                })
            
    plunge_df = pred_race[(pred_race["expected_value"] >= 1.3) & (pred_race["odds"] <= 8.5) & (pred_race["pred_score"] >= 0.55)]
    target_plunge = plunge_df.iloc[0] if len(plunge_df) > 0 else None

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
