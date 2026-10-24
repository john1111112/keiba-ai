import pandas as pd
import numpy as np
from typing import Tuple, List, Optional
from pathlib import Path

CATEGORICAL_COLS = ["race_course", "surface", "condition", "sex", "sire_type", "running_style"]

FEATURE_COLS = [
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


def _is_valid_finished_order(fo) -> bool:
    """
    完走確定着順（1〜28着）かどうかを判定する。
    未確定(0)や取消・除外・競走中止(>=29 または <=0, NaN)は過去成績の着順集計・騎手勝敗集計から除外する。
    """
    if pd.isna(fo):
        return False
    try:
        val = int(fo)
        return 1 <= val <= 28
    except Exception:
        return False


def build_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    【同日・同一レース内リーク完全排除版（開催日単位バッチ更新）】
    1. 当日の結果情報（当日の通過順位・当日の上がり3F・当日の着順）は一切特徴量に使用しない。
    2. 同じ開催日（date）の他レース結果（例: 1R〜10Rの結果を同日11Rで使用すること）も一切使用しない。
    3. 騎手累積成績（jockey_stats）・血統累積成績（sire_stats）・馬の過去走履歴（horse_hist）はすべて
       「開催日（date）単位」で、その日の全レースの特徴量算出が完了した後にのみ一括更新する。
       これにより、対象レースの推論時点では「前日（date < 当日）までに確定した過去レース」のみが100%参照される。
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(), list(FEATURE_COLS)

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "race_id", "horse_number"]).reset_index(drop=True)

    # 目的変数（1〜3着 = 1, 4着以降・未発走(0) = 0）
    df["target"] = ((df["finish_order"] >= 1) & (df["finish_order"] <= 3)).astype(int)

    # 当該レースの実測脚質（過去履歴への蓄積用に退避し、当日の特徴量には絶対に使わない）
    observed_styles = df["running_style"].fillna("先行").tolist() if "running_style" in df.columns else ["先行"] * len(df)
    df["sire"] = df["sire"].fillna("不明") if "sire" in df.columns else "不明"
    df["sire_type"] = "サンデー系"

    n_rows = len(df)
    j_win_rate = [0.08] * n_rows
    j_place_rate = [0.22] * n_rows
    sire_place_rate = [0.22] * n_rows
    sire_surface_rate = [0.22] * n_rows
    prev_ranks = [8.0] * n_rows
    prev_last_3fs = [35.5] * n_rows
    past_avg_ranks = [8.0] * n_rows
    past_place_rates = [0.20] * n_rows
    past_race_counts = [0] * n_rows
    course_suitabilities = [0.20] * n_rows
    dist_suitabilities = [0.20] * n_rows
    dir_suitabilities = [0.20] * n_rows
    heavy_suitabilities = [0.20] * n_rows
    rest_weeks_list = [8] * n_rows
    is_toushi_list = [0] * n_rows
    pre_running_styles = ["先行"] * n_rows
    has_prior_history = [False] * n_rows

    jockey_stats = {}
    sire_stats = {}
    horse_hist = {}

    # 開催日（date）単位でグループ化して処理：
    # Step A: 前日までに蓄積された統計のみを使って、その日の全出走馬の特徴量を計算
    # Step B: その日の全レースの特徴量計算が終わった後に、確定完走レース（1<=finish_order<=28）の結果を統計へ反映
    for cur_date, day_grp in df.groupby("date", sort=False):
        day_indices = day_grp.index.tolist()

        # --- Step A: 前日までの履歴のみで当日の全レースの特徴量を算出 ---
        for idx in day_indices:
            row = df.loc[idx]

            # 1. 騎手成績（前日時点）
            jid = row["jockey_id"]
            j_st = jockey_stats.get(jid)
            if j_st is not None and j_st["rides"] >= 5:
                j_win_rate[idx] = j_st["wins"] / j_st["rides"]
                j_place_rate[idx] = j_st["places"] / j_st["rides"]
            else:
                j_win_rate[idx] = 0.08
                j_place_rate[idx] = 0.22

            # 2. 血統成績（前日時点、ダミー値「キズナ」「不明」は中立値0.22）
            sire = str(row["sire"])
            surf = row["surface"]
            if sire not in ("不明", "キズナ", ""):
                s_st = sire_stats.get(sire)
                if s_st is not None:
                    if s_st["total"] >= 5:
                        sire_place_rate[idx] = s_st["places"] / s_st["total"]
                    if surf == "芝" and s_st["turf_runs"] >= 3:
                        sire_surface_rate[idx] = s_st["turf_places"] / s_st["turf_runs"]
                    elif surf == "ダート" and s_st["dirt_runs"] >= 3:
                        sire_surface_rate[idx] = s_st["dirt_places"] / s_st["dirt_runs"]

            # 3. 馬の過去走履歴（前日時点）
            hid = row["horse_id"]
            cur_course = row["race_course"]
            cur_dist = row["distance"]
            cur_dir = row["direction"]
            cur_cond = row["condition"]

            hist = horse_hist.get(hid, [])
            if len(hist) > 0:
                has_prior_history[idx] = True
                last_run = hist[-1]
                prev_ranks[idx] = float(last_run["finish_order"])
                prev_last_3fs[idx] = float(last_run["last_3f"]) if pd.notnull(last_run["last_3f"]) else 35.5

                past_styles = [r["obs_style"] for r in hist if r.get("obs_style") in ("逃げ", "先行", "差し", "追込")]
                pre_running_styles[idx] = past_styles[-1] if len(past_styles) > 0 else "先行"

                ranks = [r["finish_order"] for r in hist]
                places = [1 if r["finish_order"] <= 3 else 0 for r in hist]
                avg_place = float(np.mean(places))
                past_avg_ranks[idx] = float(np.mean(ranks))
                past_place_rates[idx] = avg_place
                past_race_counts[idx] = len(hist)

                same_course = [1 if r["finish_order"] <= 3 else 0 for r in hist if r["race_course"] == cur_course]
                course_suitabilities[idx] = float(np.mean(same_course)) if len(same_course) > 0 else avg_place

                same_dist = [1 if r["finish_order"] <= 3 else 0 for r in hist if abs(r["distance"] - cur_dist) <= 200]
                dist_suitabilities[idx] = float(np.mean(same_dist)) if len(same_dist) > 0 else avg_place

                same_dir = [1 if r["finish_order"] <= 3 else 0 for r in hist if r["direction"] == cur_dir]
                dir_suitabilities[idx] = float(np.mean(same_dir)) if len(same_dir) > 0 else avg_place

                if cur_cond in ["稍重", "重", "不良"]:
                    heavy_runs = [1 if r["finish_order"] <= 3 else 0 for r in hist if r["condition"] in ["稍重", "重", "不良"]]
                    heavy_suitabilities[idx] = float(np.mean(heavy_runs)) if len(heavy_runs) > 0 else avg_place
                else:
                    heavy_suitabilities[idx] = avg_place

                days_diff = (cur_date - last_run["date"]).days
                weeks = max(1, days_diff // 7)
                rest_weeks_list[idx] = min(weeks, 52)
                if len(hist) >= 2:
                    prev_prev_diff = (last_run["date"] - hist[-2]["date"]).days
                    is_toushi_list[idx] = 1 if prev_prev_diff >= 80 and weeks <= 6 else 0
                else:
                    is_toushi_list[idx] = 0

        # --- Step B: 当日の全レース特徴量算出完了後、完走確定結果(1<=finish_order<=28)のみを累積統計へ反映 ---
        for idx in day_indices:
            row = df.loc[idx]
            fo = row["finish_order"]
            if not _is_valid_finished_order(fo):
                continue
            fo_int = int(fo)

            # 1. 騎手統計の更新
            jid = row["jockey_id"]
            j_st = jockey_stats.get(jid, {"rides": 0, "wins": 0, "places": 0})
            j_st["rides"] += 1
            if fo_int == 1:
                j_st["wins"] += 1
            if fo_int <= 3:
                j_st["places"] += 1
            jockey_stats[jid] = j_st

            # 2. 血統統計の更新
            sire = str(row["sire"])
            surf = row["surface"]
            if sire not in ("不明", "キズナ", ""):
                s_st = sire_stats.get(sire, {"total": 0, "places": 0, "turf_runs": 0, "turf_places": 0, "dirt_runs": 0, "dirt_places": 0})
                s_st["total"] += 1
                if fo_int <= 3:
                    s_st["places"] += 1
                if surf == "芝":
                    s_st["turf_runs"] += 1
                    if fo_int <= 3:
                        s_st["turf_places"] += 1
                else:
                    s_st["dirt_runs"] += 1
                    if fo_int <= 3:
                        s_st["dirt_places"] += 1
                sire_stats[sire] = s_st

            # 3. 馬履歴の更新
            hid = row["horse_id"]
            hist = horse_hist.get(hid, [])
            hist.append({
                "finish_order": fo_int,
                "last_3f": row["last_3f"],
                "distance": row["distance"],
                "race_course": row["race_course"],
                "direction": row["direction"],
                "condition": row["condition"],
                "date": cur_date,
                "obs_style": observed_styles[idx]
            })
            horse_hist[hid] = hist

    df["jockey_win_rate"] = j_win_rate
    df["jockey_place_rate"] = j_place_rate
    df["sire_place_rate"] = sire_place_rate
    df["sire_surface_rate"] = sire_surface_rate
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
    df["has_prior_history"] = has_prior_history

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

    return df, list(FEATURE_COLS)


def build_features_for_race_with_history(race_id: str, db_path: Optional[Path] = None) -> Tuple[pd.DataFrame, List[str]]:
    """
    【単一レース推論時の過去走参照バグ修正用】
    特徴量キャッシュ（features.parquet）に対象レースが存在しない場合でも、
    単一レースの行だけで build_features() を呼ぶのではなく、
    対象レースの開催日以前（date <= target_date）の全履歴（または当該馬・当該騎手・当該血統の全過去走）を
    SQLite から取得して学習時と100%同一のロジックで特徴量を生成し、対象レースの行のみを抽出して返す。
    これにより、
    1. 未来のレース結果（date > target_date）や同日の他レース結果を一切参照しない。
    2. 過去走が存在する馬が初出走扱いになるバグを完全に防ぐ。
    3. 学習時と推論時の特徴量生成ロジックを完全に統一する。
    """
    from .database import get_connection

    conn = get_connection(db_path)
    cur = conn.cursor()
    row_date = cur.execute("SELECT date FROM races WHERE race_id = ?", (str(race_id),)).fetchone()
    if not row_date:
        conn.close()
        return pd.DataFrame(), list(FEATURE_COLS)

    target_date_str = str(row_date[0])[:10]

    # 対象レースより前の全確定レース + 対象レース自身の行のみを取得（同日の他レースや未来のレースは一切取得しない）
    query = """
    SELECT 
        r.race_id, r.race_name, r.date, r.race_course, r.race_number,
        r.surface, r.distance, r.direction, r.weather, r.condition, r.grade,
        res.finish_order, res.bracket_number, res.horse_number,
        res.horse_id, res.horse_name, res.sex, res.age, res.weight_carry,
        res.jockey_id, res.jockey_name, res.time_str, res.time_sec,
        res.margin_str, res.last_3f, res.odds, res.popularity,
        res.horse_weight, res.horse_weight_diff, res.trainer_id, res.trainer_name,
        res.sire, res.sire_type, res.running_style, res.corner_4_pos, res.rest_weeks
    FROM races r
    JOIN race_results res ON r.race_id = res.race_id
    WHERE r.date < ? OR r.race_id = ?
    ORDER BY r.date ASC, r.race_id ASC, res.horse_number ASC
    """
    df_hist_and_target = pd.read_sql_query(query, conn, params=(target_date_str, str(race_id)))
    conn.close()

    if len(df_hist_and_target) == 0:
        return pd.DataFrame(), list(FEATURE_COLS)

    feat_all, fcols = build_features(df_hist_and_target)
    race_feat = feat_all[feat_all["race_id"].astype(str) == str(race_id)].copy().reset_index(drop=True)
    return race_feat, fcols
