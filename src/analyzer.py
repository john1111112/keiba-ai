import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Any, List

def analyze_winning_conditions(df_pred: pd.DataFrame) -> Dict[str, Any]:
    """
    蓄積された確定レース（finish_order >= 1）のウォークフォワード予測結果と実着順を突き合わせ、
    条件別の勝率・複勝率・単勝回収率（JRA確定単勝配当ベース）を算出する。
    """
    # 未発走レース(finish_order == 0)およびウォークフォワード初期学習専用期間(is_warmup_block == True)を除外し、確定評価レースのみで検証
    df = df_pred[(df_pred["finish_order"] >= 1) & (df_pred["finish_order"] <= 28)].copy()
    if "is_warmup_block" in df.columns:
        df = df[~df["is_warmup_block"].fillna(False)].copy()
    if "pred_score" in df.columns:
        df = df[df["pred_score"].notna()].copy()
    if len(df) == 0:
        return {
            "overall": {"total_races": 0, "win_rate": 0.0, "place_rate": 0.0, "roi": 0.0},
            "condition_analysis": [],
            "rain_summary": {"races": 0, "win_rate": 0.0, "place_rate": 0.0, "roi": 0.0},
            "surface_analysis": [],
            "course_analysis": [],
            "confidence_analysis": [],
            "golden_patterns": []
        }

    # 本命馬（各レースでAI予測スコア1位の馬 = ◎）のみを抽出
    races = df.groupby("race_id")
    honmei_list = []

    for rid, group in races:
        sorted_g = group.sort_values("pred_score", ascending=False).reset_index(drop=True)
        if len(sorted_g) > 0:
            top = sorted_g.iloc[0].to_dict()
            top["score_margin"] = (top["pred_score"] - sorted_g.iloc[1]["pred_score"]) if len(sorted_g) > 1 else 0.1
            honmei_list.append(top)

    df_h = pd.DataFrame(honmei_list)
    df_h["is_win"] = (df_h["finish_order"] == 1).astype(int)
    df_h["is_place"] = (df_h["finish_order"].between(1, 3)).astype(int)
    df_h["return_win"] = df_h.apply(lambda r: (r["odds"] * 100) if r["is_win"] == 1 else 0, axis=1)

    total_races = len(df_h)
    overall_win_rate = df_h["is_win"].mean() * 100
    overall_place_rate = df_h["is_place"].mean() * 100
    overall_roi = (df_h["return_win"].sum() / (total_races * 100)) * 100

    # --- 1. 馬場状態別分析 ---
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

    # --- 4. AI予測確率（3着以内確率）帯別の分析 ---
    confidence_analysis = []
    bins = [0.0, 0.34, 0.42, 0.52, 1.0]
    bin_labels = ["低信頼 (<34%)", "B基準 (34-42%)", "A基準 (42-52%)", "S基準 (52%以上)"]
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

    # --- 5. 条件別セグメント実績 ---
    golden_patterns = []

    # パターンA: 雨・道悪 × ダート
    p_rain_dirt = df_h[(df_h["condition"].isin(["稍重", "重", "不良"])) & (df_h["surface"] == "ダート")]
    if len(p_rain_dirt) >= 20:
        golden_patterns.append({
            "pattern_name": "🌧️ 雨・道悪 × ダート戦",
            "description": "稍重〜不良のダート戦における本命馬(◎)の単勝・複勝実績",
            "races": len(p_rain_dirt),
            "win_rate": round(p_rain_dirt["is_win"].mean() * 100, 1),
            "place_rate": round(p_rain_dirt["is_place"].mean() * 100, 1),
            "roi": round((p_rain_dirt["return_win"].sum() / (len(p_rain_dirt) * 100)) * 100, 1)
        })

    # パターンB: AI予測3着内率52%以上（Sランク基準）
    p_iron = df_h[df_h["pred_score"] >= 0.52]
    if len(p_iron) >= 20:
        golden_patterns.append({
            "pattern_name": "🎯 AI予測複勝率52%以上（Sランク基準）",
            "description": "LightGBMの3着以内予測確率が52%以上の本命馬(◎)の実績",
            "races": len(p_iron),
            "win_rate": round(p_iron["is_win"].mean() * 100, 1),
            "place_rate": round(p_iron["is_place"].mean() * 100, 1),
            "roi": round((p_iron["return_win"].sum() / (len(p_iron) * 100)) * 100, 1)
        })

    # パターンC: 期待値1.5以上の中穴馬 (単勝8〜25倍)
    ana_all = df[(df["expected_value"] >= 1.5) & (df["odds"] >= 8.0) & (df["odds"] <= 25.0)].copy()
    if len(ana_all) >= 20:
        ana_win = (ana_all["finish_order"] == 1).astype(int)
        ana_place = (ana_all["finish_order"].between(1, 3)).astype(int)
        ana_ret = ana_win * ana_all["odds"] * 100
        golden_patterns.append({
            "pattern_name": "💎 期待値1.5以上の中穴馬 (単勝8〜25倍)",
            "description": "AIの3着内予測確率×単勝オッズが1.5以上の中穴馬の単勝実績",
            "races": len(ana_all),
            "win_rate": round(ana_win.mean() * 100, 1),
            "place_rate": round(ana_place.mean() * 100, 1),
            "roi": round((ana_ret.sum() / (len(ana_all) * 100)) * 100, 1)
        })

    # パターンD: 芝マイル(1600m)戦
    p_mile = df_h[(df_h["distance"] == 1600) & (df_h["surface"] == "芝")]
    if len(p_mile) >= 20:
        golden_patterns.append({
            "pattern_name": "⚡ 芝1600m (マイル戦)",
            "description": "芝1600m戦における本命馬(◎)の単勝・複勝実績",
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
