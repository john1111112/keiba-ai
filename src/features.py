import pandas as pd
import numpy as np
from typing import Tuple, List

CATEGORICAL_COLS = ["race_course", "surface", "condition", "sex", "sire_type", "running_style"]

def build_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    【リーク完全排除版】
    当日の結果情報（当日の通過順位・当日の上がり3F・当日の着順）を一切特徴量に混ぜず、
    当該レースより前の過去走履歴のみから脚質・逃げ馬頭数・騎手成績・馬適性を算出する。
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "race_id", "horse_number"]).reset_index(drop=True)

    # 目的変数（1〜3着 = 1, 4着以降・未発走(0) = 0）
    df["target"] = ((df["finish_order"] >= 1) & (df["finish_order"] <= 3)).astype(int)

    # 当該レースの実測脚質（過去履歴への蓄積用に退避し、当日の特徴量には絶対に使わない）
    observed_styles = df["running_style"].fillna("先行").tolist() if "running_style" in df.columns else ["先行"] * len(df)
    df["sire"] = df["sire"].fillna("不明") if "sire" in df.columns else "不明"
    df["sire_type"] = "サンデー系"

    # 1. 騎手の累積勝率・複勝率（当日より前の確定レースのみから累積）
    jockey_stats = {}
    j_win_rate, j_place_rate = [], []
    for idx, row in df.iterrows():
        jid = row["jockey_id"]
        stats = jockey_stats.get(jid, {"rides": 0, "wins": 0, "places": 0})
        if stats["rides"] >= 5:
            j_win_rate.append(stats["wins"] / stats["rides"])
            j_place_rate.append(stats["places"] / stats["rides"])
        else:
            j_win_rate.append(0.08)
            j_place_rate.append(0.22)
        if row["finish_order"] >= 1:
            stats["rides"] += 1
            if row["finish_order"] == 1:
                stats["wins"] += 1
            if row["finish_order"] <= 3:
                stats["places"] += 1
            jockey_stats[jid] = stats
    df["jockey_win_rate"] = j_win_rate
    df["jockey_place_rate"] = j_place_rate

    # 2. 血統指標（ダミー値「キズナ」や「不明」の場合は中立値0.22に固定してノイズを排除）
    sire_stats = {}
    sire_place_rate, sire_surface_rate = [], []
    for idx, row in df.iterrows():
        sire = str(row["sire"])
        surf = row["surface"]
        if sire in ("不明", "キズナ", ""):
            sire_place_rate.append(0.22)
            sire_surface_rate.append(0.22)
            continue
        s_data = sire_stats.get(sire, {"total": 0, "places": 0, "turf_runs": 0, "turf_places": 0, "dirt_runs": 0, "dirt_places": 0})
        sire_place_rate.append(s_data["places"] / s_data["total"] if s_data["total"] >= 5 else 0.22)
        if surf == "芝" and s_data["turf_runs"] >= 3:
            sire_surface_rate.append(s_data["turf_places"] / s_data["turf_runs"])
        elif surf == "ダート" and s_data["dirt_runs"] >= 3:
            sire_surface_rate.append(s_data["dirt_places"] / s_data["dirt_runs"])
        else:
            sire_surface_rate.append(0.22)
        if row["finish_order"] >= 1:
            s_data["total"] += 1
            if row["finish_order"] <= 3:
                s_data["places"] += 1
            if surf == "芝":
                s_data["turf_runs"] += 1
                if row["finish_order"] <= 3:
                    s_data["turf_places"] += 1
            else:
                s_data["dirt_runs"] += 1
                if row["finish_order"] <= 3:
                    s_data["dirt_places"] += 1
            sire_stats[sire] = s_data

    df["sire_place_rate"] = sire_place_rate
    df["sire_surface_rate"] = sire_surface_rate

    # 3. 馬ごとの過去成績・適性・ローテーション・事前脚質（100%過去走のみから算出）
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
    pre_running_styles = []

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
            prev_last_3fs.append(last_run["last_3f"] if pd.notnull(last_run["last_3f"]) else 35.5)

            # 過去走の脚質から事前脚質を決定（当日の通過順位は一切見ない）
            past_styles = [r["obs_style"] for r in hist if r.get("obs_style") in ("逃げ", "先行", "差し", "追込")]
            pre_running_styles.append(past_styles[-1] if len(past_styles) > 0 else "先行")

            ranks = [r["finish_order"] for r in hist]
            places = [1 if r["finish_order"] <= 3 else 0 for r in hist]
            past_avg_ranks.append(np.mean(ranks))
            past_place_rates.append(np.mean(places))
            past_race_counts.append(len(hist))

            same_course = [1 if r["finish_order"] <= 3 else 0 for r in hist if r["race_course"] == cur_course]
            course_suitabilities.append(np.mean(same_course) if len(same_course) > 0 else np.mean(places))

            same_dist = [1 if r["finish_order"] <= 3 else 0 for r in hist if abs(r["distance"] - cur_dist) <= 200]
            dist_suitabilities.append(np.mean(same_dist) if len(same_dist) > 0 else np.mean(places))

            same_dir = [1 if r["finish_order"] <= 3 else 0 for r in hist if r["direction"] == cur_dir]
            dir_suitabilities.append(np.mean(same_dir) if len(same_dir) > 0 else np.mean(places))

            if cur_cond in ["稍重", "重", "不良"]:
                heavy_runs = [1 if r["finish_order"] <= 3 else 0 for r in hist if r["condition"] in ["稍重", "重", "不良"]]
                heavy_suitabilities.append(np.mean(heavy_runs) if len(heavy_runs) > 0 else np.mean(places))
            else:
                heavy_suitabilities.append(np.mean(places))

            days_diff = (cur_date - last_run["date"]).days
            weeks = max(1, days_diff // 7)
            rest_weeks_list.append(min(weeks, 52))
            if len(hist) >= 2:
                prev_prev_diff = (last_run["date"] - hist[-2]["date"]).days
                is_toushi_list.append(1 if prev_prev_diff >= 80 and weeks <= 6 else 0)
            else:
                is_toushi_list.append(0)
        else:
            prev_ranks.append(8.0)
            prev_last_3fs.append(35.5)
            pre_running_styles.append("先行")
            past_avg_ranks.append(8.0)
            past_place_rates.append(0.20)
            past_race_counts.append(0)
            course_suitabilities.append(0.20)
            dist_suitabilities.append(0.20)
            dir_suitabilities.append(0.20)
            heavy_suitabilities.append(0.20)
            rest_weeks_list.append(8)
            is_toushi_list.append(0)

        if row["finish_order"] >= 1:
            hist.append({
                "finish_order": row["finish_order"],
                "last_3f": row["last_3f"],
                "distance": cur_dist,
                "race_course": cur_course,
                "direction": cur_dir,
                "condition": cur_cond,
                "date": cur_date,
                "obs_style": observed_styles[idx]
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
    df["running_style"] = pre_running_styles

    # 4. レース内の展開・ペース予測（過去走で逃げ脚質だった馬の頭数のみから算出）
    nige_counts = df[df["running_style"] == "逃げ"].groupby("race_id")["horse_number"].count().to_dict()
    df["race_nige_count"] = df["race_id"].map(lambda rid: nige_counts.get(rid, 0))
    df["is_fast_pace"] = (df["race_nige_count"] >= 3).astype(int)

    # 5. 身体・当日ファクター
    df["horse_weight"] = df["horse_weight"].fillna(480.0)
    df["horse_weight_diff"] = df["horse_weight_diff"].fillna(0.0)
    df["weight_carry"] = df["weight_carry"].fillna(55.0)
    df["weight_ratio"] = df["weight_carry"] / df["horse_weight"]

    cat_defs = {
        "sire_type": ["A.P.Indy系", "キンカメ系", "サンデー系", "ミスプロ系", "ロベルト系"],
        "running_style": ["先行", "差し", "追込", "逃げ"],
        "race_course": ["中京", "中山", "京都", "函館", "小倉", "新潟", "札幌", "東京", "福島", "阪神"],
        "surface": ["ダート", "芝"],
        "condition": ["不良", "稍重", "良", "重"],
        "sex": ["セン", "牝", "牡"],
    }
    for col in CATEGORICAL_COLS:
        if col in cat_defs:
            df[col] = pd.Categorical(df[col], categories=cat_defs[col])
        else:
            df[col] = df[col].astype("category")

    feature_cols = [
        "bracket_number", "horse_number", "age", "weight_carry",
        "distance", "horse_weight", "horse_weight_diff", "weight_ratio",
        "jockey_win_rate", "jockey_place_rate",
        "prev_rank", "prev_last_3f", "past_avg_rank", "past_place_rate", "past_race_count",
        "course_place_rate", "dist_place_rate", "direction_place_rate", "heavy_place_rate",
        "sire_place_rate", "sire_surface_rate", "sire_type",
        "running_style", "race_nige_count", "is_fast_pace",
        "rest_weeks", "is_toushi",
        "race_course", "surface", "condition", "sex"
    ]

    return df, feature_cols
