import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Any, List

def analyze_winning_conditions(df_pred: pd.DataFrame) -> Dict[str, Any]:
    """
    蓄積された全レースのAI予測結果と実着順を突き合わせ、
    AIの勝率・複勝率・単勝回収率が跳ね上がる『黄金条件』を多角的に抽出・分析する。
    """
    df = df_pred.copy()
    
    # 本命馬（各レースでAI予測スコア1位の馬 = ◎）のみを抽出
    races = df.groupby("race_id")
    honmei_list = []
    
    for rid, group in races:
        sorted_g = group.sort_values("pred_score", ascending=False).reset_index(drop=True)
        if len(sorted_g) > 0:
            top = sorted_g.iloc[0].to_dict()
            # 2位とのスコア差（AIの自信度）
            top["score_margin"] = (top["pred_score"] - sorted_g.iloc[1]["pred_score"]) if len(sorted_g) > 1 else 0.5
            honmei_list.append(top)
            
    df_h = pd.DataFrame(honmei_list)
    df_h["is_win"] = (df_h["finish_order"] == 1).astype(int)
    df_h["is_place"] = (df_h["finish_order"] <= 3).astype(int)
    df_h["return_win"] = df_h.apply(lambda r: (r["odds"] * 100) if r["is_win"] == 1 else 0, axis=1)
    
    # 総合基本成績
    total_races = len(df_h)
    overall_win_rate = df_h["is_win"].mean() * 100
    overall_place_rate = df_h["is_place"].mean() * 100
    overall_roi = (df_h["return_win"].sum() / (total_races * 100)) * 100
    
    # --- 1. 馬場状態別分析 (雨の日 vs 晴れ) ---
    cond_analysis = []
    for cond in ["良", "稍重", "重", "不良"]:
        sub = df_h[df_h["condition"] == cond]
        if len(sub) > 0:
            w_rate = sub["is_win"].mean() * 100
            p_rate = sub["is_place"].mean() * 100
            roi = (sub["return_win"].sum() / (len(sub) * 100)) * 100
            cond_analysis.append({
                "condition": cond,
                "races": len(sub),
                "win_rate": round(w_rate, 1),
                "place_rate": round(p_rate, 1),
                "roi": round(roi, 1),
                "is_rain": cond in ["稍重", "重", "不良"]
            })
            
    # 道悪（雨・稍重〜不良まとめ）
    rain_sub = df_h[df_h["condition"].isin(["稍重", "重", "不良"])]
    rain_summary = {
        "races": len(rain_sub),
        "win_rate": round(rain_sub["is_win"].mean() * 100, 1) if len(rain_sub) > 0 else 0,
        "place_rate": round(rain_sub["is_place"].mean() * 100, 1) if len(rain_sub) > 0 else 0,
        "roi": round((rain_sub["return_win"].sum() / (len(rain_sub) * 100)) * 100, 1) if len(rain_sub) > 0 else 0
    }

    # --- 2. 芝 vs ダート別分析 ---
    surf_analysis = []
    for surf in ["芝", "ダート"]:
        sub = df_h[df_h["surface"] == surf]
        if len(sub) > 0:
            surf_analysis.append({
                "surface": surf,
                "races": len(sub),
                "win_rate": round(sub["is_win"].mean() * 100, 1),
                "place_rate": round(sub["is_place"].mean() * 100, 1),
                "roi": round((sub["return_win"].sum() / (len(sub) * 100)) * 100, 1)
            })

    # --- 3. 競馬場別分析 ---
    course_analysis = []
    for c in df_h["race_course"].unique():
        sub = df_h[df_h["race_course"] == c]
        if len(sub) >= 20:
            course_analysis.append({
                "course": c,
                "races": len(sub),
                "win_rate": round(sub["is_win"].mean() * 100, 1),
                "place_rate": round(sub["is_place"].mean() * 100, 1),
                "roi": round((sub["return_win"].sum() / (len(sub) * 100)) * 100, 1)
            })
    course_analysis.sort(key=lambda x: x["place_rate"], reverse=True)

    # --- 4. AI自信度（予測スコア別）分析 ---
    confidence_analysis = []
    bins = [0.0, 0.4, 0.6, 0.75, 1.0]
    bin_labels = ["普通 (好走率<40%)", "中自信 (40-60%)", "高自信 (60-75%)", "超鉄板 (75%以上)"]
    df_h["conf_bin"] = pd.cut(df_h["pred_score"], bins=bins, labels=bin_labels)
    for b in bin_labels:
        sub = df_h[df_h["conf_bin"] == b]
        if len(sub) > 0:
            confidence_analysis.append({
                "confidence": b,
                "races": len(sub),
                "win_rate": round(sub["is_win"].mean() * 100, 1),
                "place_rate": round(sub["is_place"].mean() * 100, 1),
                "roi": round((sub["return_win"].sum() / (len(sub) * 100)) * 100, 1)
            })

    # --- 5. 跳ね上がる『神パターン』の自動抽出 (Best Patterns) ---
    golden_patterns = []
    
    # パターンA: 雨の日 × ダート
    p_rain_dirt = df_h[(df_h["condition"].isin(["稍重", "重", "不良"])) & (df_h["surface"] == "ダート")]
    if len(p_rain_dirt) >= 30:
        golden_patterns.append({
            "pattern_name": "🌧️ 雨・道悪 × ダート戦",
            "description": "雨で脚抜きが良くなったダートでは先行力と道悪血統が強く反映され、AIの予測が劇的にハマる",
            "races": len(p_rain_dirt),
            "win_rate": round(p_rain_dirt["is_win"].mean() * 100, 1),
            "place_rate": round(p_rain_dirt["is_place"].mean() * 100, 1),
            "roi": round((p_rain_dirt["return_win"].sum() / (len(p_rain_dirt) * 100)) * 100, 1)
        })

    # パターンB: AI好走率75%以上の超鉄板レース
    p_iron = df_h[df_h["pred_score"] >= 0.75]
    if len(p_iron) >= 30:
        golden_patterns.append({
            "pattern_name": "🎯 AI好走率75%以上の『超鉄板』レース",
            "description": "他馬を能力・適性・騎手で圧倒しているレース。複勝圏内の信頼度が極めて高い",
            "races": len(p_iron),
            "win_rate": round(p_iron["is_win"].mean() * 100, 1),
            "place_rate": round(p_iron["is_place"].mean() * 100, 1),
            "roi": round((p_iron["return_win"].sum() / (len(p_iron) * 100)) * 100, 1)
        })

    # パターンC: 単穴・穴馬(☆)で期待値1.8以上の高期待値狙い
    ana_all = df[(df["expected_value"] >= 1.8) & (df["odds"] >= 6.0) & (df["odds"] <= 25.0)].copy()
    if len(ana_all) >= 30:
        ana_win = (ana_all["finish_order"] == 1).astype(int)
        ana_place = (ana_all["finish_order"] <= 3).astype(int)
        ana_ret = ana_win * ana_all["odds"] * 100
        golden_patterns.append({
            "pattern_name": "💎 期待値1.8以上の中穴馬 (単勝6〜25倍)",
            "description": "AIが実力を高く評価しているのにオッズが舐められている過小評価馬の単勝狙い",
            "races": len(ana_all),
            "win_rate": round(ana_win.mean() * 100, 1),
            "place_rate": round(ana_place.mean() * 100, 1),
            "roi": round((ana_ret.sum() / (len(ana_all) * 100)) * 100, 1)
        })
        
    # パターンD: 芝マイル(1600m)戦
    p_mile = df_h[(df_h["distance"] == 1600) & (df_h["surface"] == "芝")]
    if len(p_mile) >= 30:
        golden_patterns.append({
            "pattern_name": "⚡ 芝1600m (マイル戦)",
            "description": "実力差や上がり3Fのキレが素直に反映されやすく、紛れが少ない王道コース",
            "races": len(p_mile),
            "win_rate": round(p_mile["is_win"].mean() * 100, 1),
            "place_rate": round(p_mile["is_place"].mean() * 100, 1),
            "roi": round((p_mile["return_win"].sum() / (len(p_mile) * 100)) * 100, 1)
        })

    return {
        "overall": {
            "total_races": total_races,
            "win_rate": round(overall_win_rate, 1),
            "place_rate": round(overall_place_rate, 1),
            "roi": round(overall_roi, 1)
        },
        "condition_analysis": cond_analysis,
        "rain_summary": rain_summary,
        "surface_analysis": surf_analysis,
        "course_analysis": course_analysis,
        "confidence_analysis": confidence_analysis,
        "golden_patterns": golden_patterns
    }

if __name__ == "__main__":
    from pathlib import Path
    import pandas as pd
    from src.model import KeibaAIModel
    
    p = Path("data/features.parquet")
    if p.exists():
        df = pd.read_parquet(p)
        model = KeibaAIModel()
        pred = model.predict_race(df)
        res = analyze_winning_conditions(pred)
        print("Overall:", res["overall"])
        print("Rain Summary:", res["rain_summary"])
        print("Golden Patterns:")
        for gp in res["golden_patterns"]:
            print(f"  {gp['pattern_name']}: 勝率={gp['win_rate']}%, 複勝率={gp['place_rate']}%, 回収率={gp['roi']}%")
