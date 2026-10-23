import pandas as pd
import numpy as np
from typing import Dict, Any, List

def run_combo_backtest(df_evaluated: pd.DataFrame) -> Dict[str, Any]:
    """
    ワイド流し、馬連流し、3連複フォーメーションの高勝率・高回収率シミュレーション
    """
    grouped = df_evaluated.groupby("race_id")
    total_races = len(grouped)
    
    # 1. ワイド流し (◎から○▲☆へ3点)
    wide_bets = 0
    wide_returns = 0
    wide_hits = 0
    wide_double_hits = 0
    
    # 2. 馬連流し (◎から○▲☆へ3点)
    umaren_bets = 0
    umaren_returns = 0
    umaren_hits = 0
    
    # 3. 3連複フォーメーション (1列目:◎, 2列目:○▲, 3列目:○▲△☆ 計5点)
    sanrenpuku_bets = 0
    sanrenpuku_returns = 0
    sanrenpuku_hits = 0
    
    for rid, group in grouped:
        sorted_g = group.sort_values("pred_score", ascending=False).reset_index(drop=True)
        if len(sorted_g) < 6:
            continue
            
        top1 = sorted_g.iloc[0]  # ◎
        top2 = sorted_g.iloc[1]  # ○
        top3 = sorted_g.iloc[2]  # ▲
        top4 = sorted_g.iloc[3]  # △
        
        # 穴馬(☆)
        ana_horses = sorted_g[(sorted_g["expected_value"] >= 1.5) & (sorted_g["odds"] >= 6.0)]
        top_ana = ana_horses.iloc[0] if len(ana_horses) > 0 else sorted_g.iloc[4]
        
        # 実際の上位着順の馬番
        first_h = sorted_g[sorted_g["finish_order"] == 1]
        second_h = sorted_g[sorted_g["finish_order"] == 2]
        third_h = sorted_g[sorted_g["finish_order"] == 3]
        
        if len(first_h) == 0 or len(second_h) == 0 or len(third_h) == 0:
            continue
            
        h1 = first_h.iloc[0]["horse_number"]
        h2 = second_h.iloc[0]["horse_number"]
        h3 = third_h.iloc[0]["horse_number"]
        top3_nums = {h1, h2, h3}
        
        # ---------------------------------------------
        # 1. ワイド流し (◎から ○, ▲, ☆ への3点流し 各300円 = 900円投資)
        # ---------------------------------------------
        wide_bets += 900
        wide_partners = [top2["horse_number"], top3["horse_number"], top_ana["horse_number"]]
        w_hit_count = 0
        w_ret_this = 0
        
        if top1["horse_number"] in top3_nums:
            for p_num in wide_partners:
                if p_num in top3_nums:
                    w_hit_count += 1
                    # ワイドオッズ概算 (単勝オッズ積の0.35〜0.45倍)
                    p_odds = next((h["odds"] for _, h in sorted_g.iterrows() if h["horse_number"] == p_num), 5.0)
                    approx_w_odds = max(1.8, round(np.sqrt(top1["odds"] * p_odds) * 1.2, 1))
                    w_ret_this += int(approx_w_odds * 300)
                    
        if w_hit_count > 0:
            wide_hits += 1
            wide_returns += w_ret_this
            if w_hit_count >= 2:
                wide_double_hits += 1

        # ---------------------------------------------
        # 2. 馬連流し (◎から ○, ▲, ☆ への3点流し 各300円 = 900円投資)
        # ---------------------------------------------
        umaren_bets += 900
        top2_nums = {h1, h2}
        if top1["horse_number"] in top2_nums:
            for p_num in wide_partners:
                if p_num in top2_nums:
                    umaren_hits += 1
                    p_odds = next((h["odds"] for _, h in sorted_g.iterrows() if h["horse_number"] == p_num), 5.0)
                    approx_uma_odds = max(3.5, round((top1["odds"] * p_odds) * 0.55, 1))
                    umaren_returns += int(approx_uma_odds * 300)
                    break

        # ---------------------------------------------
        # 3. 3連複フォーメーション (1列目:◎, 2列目:○▲, 3列目:○▲△☆ 計5点 各200円 = 1,000円投資)
        # ---------------------------------------------
        sanrenpuku_bets += 1000
        # 買い目5点: (◎,○,▲), (◎,○,△), (◎,○,☆), (◎,▲,△), (◎,▲,☆)
        combos = [
            {top1["horse_number"], top2["horse_number"], top3["horse_number"]},
            {top1["horse_number"], top2["horse_number"], top4["horse_number"]},
            {top1["horse_number"], top2["horse_number"], top_ana["horse_number"]},
            {top1["horse_number"], top3["horse_number"], top4["horse_number"]},
            {top1["horse_number"], top3["horse_number"], top_ana["horse_number"]},
        ]
        
        hit_sanren = False
        for c in combos:
            if c == top3_nums:
                hit_sanren = True
                # 3連複オッズ概算 (単勝オッズの幾何平均ベース)
                o1 = top1["odds"]
                o2 = next((h["odds"] for _, h in sorted_g.iterrows() if h["horse_number"] == list(c)[1]), 4.0)
                o3 = next((h["odds"] for _, h in sorted_g.iterrows() if h["horse_number"] == list(c)[2]), 8.0)
                approx_3p_odds = max(8.0, round((o1 * o2 * o3) ** 0.65 * 1.8, 1))
                sanrenpuku_returns += int(approx_3p_odds * 200)
                break
                
        if hit_sanren:
            sanrenpuku_hits += 1

    r_count = wide_bets // 900
    return {
        "total_races": r_count,
        "wide": {
            "races": r_count,
            "bets": wide_bets,
            "returns": wide_returns,
            "profit": wide_returns - wide_bets,
            "hit_rate": round(wide_hits / r_count * 100, 1),
            "double_hit_rate": round(wide_double_hits / r_count * 100, 1),
            "recovery_rate": round(wide_returns / wide_bets * 100, 1),
            "hits": wide_hits
        },
        "umaren": {
            "races": r_count,
            "bets": umaren_bets,
            "returns": umaren_returns,
            "profit": umaren_returns - umaren_bets,
            "hit_rate": round(umaren_hits / r_count * 100, 1),
            "recovery_rate": round(umaren_returns / umaren_bets * 100, 1),
            "hits": umaren_hits
        },
        "sanrenpuku": {
            "races": r_count,
            "bets": sanrenpuku_bets,
            "returns": sanrenpuku_returns,
            "profit": sanrenpuku_returns - sanrenpuku_bets,
            "hit_rate": round(sanrenpuku_hits / r_count * 100, 1),
            "recovery_rate": round(sanrenpuku_returns / sanrenpuku_bets * 100, 1),
            "hits": sanrenpuku_hits
        }
    }

if __name__ == "__main__":
    p = Path("data/features.parquet")
    if p.exists():
        df = pd.read_parquet(p)
        from src.model import KeibaAIModel
        m = KeibaAIModel()
        pred = m.predict_race(df.iloc[-30000:])
        res = run_combo_backtest(pred)
        print("Wide:", res["wide"])
        print("Umaren:", res["umaren"])
        print("Sanrenpuku:", res["sanrenpuku"])
