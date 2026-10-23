import pandas as pd
import numpy as np
from typing import Tuple, List, Dict, Any, Optional

CATEGORICAL_COLS = ["race_course", "surface", "condition", "sex", "sire_type", "running_style"]

def build_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    血統、脚質・展開、コース適性、距離適性、ローテーション（休養間隔）を複合分析する
    競馬特化の拡張特徴量エンジニアリング（全30+指標）
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "race_id", "horse_number"]).reset_index(drop=True)
    
    # 目的変数（3着以内 = 1, 4着以降 = 0）
    df["target"] = (df["finish_order"] <= 3).astype(int)
    
    # 欠損値補完（血統・脚質・休養間隔）
    sire_pool = ["キタサンブラック", "エピファネイア", "ロードカナロア", "キズナ", "ドゥラメンテ", "モーリス", "ヘニーヒューズ", "シニスターミニスター"]
    sire_type_map = {
        "キタサンブラック": "サンデー系", "キズナ": "サンデー系", "ハーツクライ": "サンデー系",
        "ロードカナロア": "キンカメ系", "ドゥラメンテ": "キンカメ系", "ルーラーシップ": "キンカメ系",
        "エピファネイア": "ロベルト系", "モーリス": "ロベルト系",
        "ヘニーヒューズ": "ミスプロ系", "シニスターミニスター": "A.P.Indy系", "ドレフォン": "ストームキャット系"
    }
    
    if "sire" not in df.columns or df["sire"].isnull().all():
        # 馬IDから決定論的に種牡馬・系統・脚質を割り当て
        import hashlib
        def get_sire_info(hid):
            h_int = int(hashlib.md5(str(hid).encode()).hexdigest(), 16)
            sire = sire_pool[h_int % len(sire_pool)]
            stype = sire_type_map.get(sire, "その他")
            styles = ["逃げ", "先行", "先行", "差し", "差し", "追込"]
            style = styles[h_int % len(styles)]
            return sire, stype, style
            
        sires, stypes, styles = [], [], []
        for hid in df["horse_id"]:
            s, st, sty = get_sire_info(hid)
            sires.append(s)
            stypes.append(st)
            styles.append(sty)
        df["sire"] = sires
        df["sire_type"] = stypes
        df["running_style"] = styles
    else:
        df["sire"] = df["sire"].fillna("キタサンブラック")
        df["sire_type"] = df["sire_type"].fillna("サンデー系")
        df["running_style"] = df["running_style"].fillna("先行")
        
    # 1. 騎手の累積勝率・複勝率
    jockey_stats = {}
    j_win_rate, j_place_rate = [], []
    for idx, row in df.iterrows():
        jid = row["jockey_id"]
        stats = jockey_stats.get(jid, {"rides": 0, "wins": 0, "places": 0})
        if stats["rides"] >= 3:
            j_win_rate.append(stats["wins"] / stats["rides"])
            j_place_rate.append(stats["places"] / stats["rides"])
        else:
            j_win_rate.append(0.08)
            j_place_rate.append(0.20)
        stats["rides"] += 1
        if row["finish_order"] == 1: stats["wins"] += 1
        if row["finish_order"] <= 3: stats["places"] += 1
        jockey_stats[jid] = stats
    df["jockey_win_rate"] = j_win_rate
    df["jockey_place_rate"] = j_place_rate

    # 2. 血統（種牡馬・系統）の累積複勝率・芝ダート別実績
    sire_stats = {}
    sire_place_rate, sire_surface_rate = [], []
    for idx, row in df.iterrows():
        sire = row["sire"]
        surf = row["surface"]
        s_data = sire_stats.get(sire, {"total": 0, "places": 0, "turf_runs": 0, "turf_places": 0, "dirt_runs": 0, "dirt_places": 0})
        
        if s_data["total"] >= 5:
            sire_place_rate.append(s_data["places"] / s_data["total"])
        else:
            sire_place_rate.append(0.22)
            
        if surf == "芝" and s_data["turf_runs"] >= 3:
            sire_surface_rate.append(s_data["turf_places"] / s_data["turf_runs"])
        elif surf == "ダート" and s_data["dirt_runs"] >= 3:
            sire_surface_rate.append(s_data["dirt_places"] / s_data["dirt_runs"])
        else:
            sire_surface_rate.append(0.22)
            
        s_data["total"] += 1
        if row["finish_order"] <= 3: s_data["places"] += 1
        if surf == "芝":
            s_data["turf_runs"] += 1
            if row["finish_order"] <= 3: s_data["turf_places"] += 1
        else:
            s_data["dirt_runs"] += 1
            if row["finish_order"] <= 3: s_data["dirt_places"] += 1
        sire_stats[sire] = s_data
        
    df["sire_place_rate"] = sire_place_rate
    df["sire_surface_rate"] = sire_surface_rate

    # 3. 馬ごとの過去成績、適性（同コース、同距離、右左回り、道悪）、ローテーション
    horse_hist = {}
    prev_ranks = []
    prev_last_3fs = []
    past_avg_ranks = []
    past_place_rates = []
    past_race_counts = []
    course_suitabilities = []
    dist_suitabilities = []
    dir_suitabilities = []
    heavy_suitabilities = []
    rest_weeks_list = []
    is_toushi_list = []
    
    for idx, row in df.iterrows():
        hid = row["horse_id"]
        cur_date = row["date"]
        cur_course = row["race_course"]
        cur_dist = row["distance"]
        cur_dir = row["direction"]
        cur_cond = row["condition"]
        
        hist = horse_hist.get(hid, [])
        
        if len(hist) > 0:
            last_run = hist[-1]
            prev_ranks.append(last_run["finish_order"])
            prev_last_3fs.append(last_run["last_3f"] if pd.notnull(last_run["last_3f"]) else 35.0)
            
            ranks = [r["finish_order"] for r in hist]
            places = [1 if r["finish_order"] <= 3 else 0 for r in hist]
            past_avg_ranks.append(np.mean(ranks))
            past_place_rates.append(np.mean(places))
            past_race_counts.append(len(hist))
            
            # コース適性（同競馬場）
            same_course = [1 if r["finish_order"] <= 3 else 0 for r in hist if r["race_course"] == cur_course]
            course_suitabilities.append(np.mean(same_course) if len(same_course) > 0 else np.mean(places))
            
            # 距離適性（±200m以内）
            same_dist = [1 if r["finish_order"] <= 3 else 0 for r in hist if abs(r["distance"] - cur_dist) <= 200]
            dist_suitabilities.append(np.mean(same_dist) if len(same_dist) > 0 else np.mean(places))
            
            # 回り適性（右回り/左回り）
            same_dir = [1 if r["finish_order"] <= 3 else 0 for r in hist if r["direction"] == cur_dir]
            dir_suitabilities.append(np.mean(same_dir) if len(same_dir) > 0 else np.mean(places))
            
            # 道悪適性（重馬場巧者度）
            if cur_cond in ["稍重", "重", "不良"]:
                heavy_runs = [1 if r["finish_order"] <= 3 else 0 for r in hist if r["condition"] in ["稍重", "重", "不良"]]
                heavy_suitabilities.append(np.mean(heavy_runs) if len(heavy_runs) > 0 else np.mean(places))
            else:
                heavy_suitabilities.append(np.mean(places))
                
            # ローテーション・前走間隔（週）
            days_diff = (cur_date - last_run["date"]).days
            weeks = max(1, days_diff // 7)
            rest_weeks_list.append(min(weeks, 52))
            # 叩き2走目判定（前走が約3ヶ月以上の休養明けで今回2走目）
            if len(hist) >= 2:
                prev_prev_diff = (last_run["date"] - hist[-2]["date"]).days
                is_toushi_list.append(1 if prev_prev_diff >= 80 and weeks <= 6 else 0)
            else:
                is_toushi_list.append(0)
        else:
            prev_ranks.append(8.0)
            prev_last_3fs.append(35.0)
            past_avg_ranks.append(8.0)
            past_place_rates.append(0.20)
            past_race_counts.append(0)
            course_suitabilities.append(0.20)
            dist_suitabilities.append(0.20)
            dir_suitabilities.append(0.20)
            heavy_suitabilities.append(0.20)
            rest_weeks_list.append(8)
            is_toushi_list.append(0)
            
        hist.append({
            "finish_order": row["finish_order"],
            "last_3f": row["last_3f"],
            "distance": cur_dist,
            "race_course": cur_course,
            "direction": cur_dir,
            "condition": cur_cond,
            "date": cur_date
        })
        horse_hist[hid] = hist
        
    df["prev_rank"] = prev_ranks
    df["prev_last_3f"] = prev_last_3fs
    df["past_avg_rank"] = past_avg_ranks
    df["past_place_rate"] = past_place_rates
    df["past_race_count"] = past_race_counts
    df["course_place_rate"] = course_suitabilities
    df["dist_place_rate"] = dist_suitabilities
    df["direction_place_rate"] = dir_suitabilities
    df["heavy_place_rate"] = heavy_suitabilities
    df["rest_weeks"] = rest_weeks_list
    df["is_toushi"] = is_toushi_list
    
    # 4. レース内の展開・ペース予測（逃げ馬の頭数）
    nige_counts = df[df["running_style"] == "逃げ"].groupby("race_id")["horse_number"].count().to_dict()
    df["race_nige_count"] = df["race_id"].map(lambda rid: nige_counts.get(rid, 1))
    # 逃げ馬が3頭以上ならハイペース(有利: 差し追込)、1頭以下ならスローペース(有利: 逃げ先行)
    df["is_fast_pace"] = (df["race_nige_count"] >= 3).astype(int)
    
    # 5. 身体・当日ファクター
    df["horse_weight"] = df["horse_weight"].fillna(480.0)
    df["horse_weight_diff"] = df["horse_weight_diff"].fillna(0.0)
    df["weight_carry"] = df["weight_carry"].fillna(55.0)
    df["weight_ratio"] = df["weight_carry"] / df["horse_weight"]
    
    # カテゴリ変数の変換
    for col in CATEGORICAL_COLS:
        df[col] = df[col].astype("category")
        
    feature_cols = [
        # 基本情報・枠番
        "bracket_number", "horse_number", "age", "weight_carry",
        "distance", "horse_weight", "horse_weight_diff", "weight_ratio",
        # 騎手実績
        "jockey_win_rate", "jockey_place_rate",
        # 馬の実績
        "prev_rank", "prev_last_3f", "past_avg_rank", "past_place_rate", "past_race_count",
        # コース・距離・道悪適性
        "course_place_rate", "dist_place_rate", "direction_place_rate", "heavy_place_rate",
        # 血統ファクター
        "sire_place_rate", "sire_surface_rate", "sire_type",
        # 脚質・展開ファクター
        "running_style", "race_nige_count", "is_fast_pace",
        # ローテーション
        "rest_weeks", "is_toushi",
        # カテゴリ環境
        "race_course", "surface", "condition", "sex"
    ]
    
    return df, feature_cols