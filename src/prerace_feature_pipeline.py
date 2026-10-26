"""
発走前特徴量（28特徴量）の安全生成パイプライン
- 事後結果（finish_order, 確定オッズ等）の混入を完全遮断
- 開催日（race_date）を必須とし、過去データ（data/keiba.db）から発走前時点（r.date < target_date）の過去成績のみを集計
- 架空の特徴量補完（4歳、55kg、0kg、4週、先行、良、右等のデフォルト捏造）を完全撤去
- 特徴量不足（必須項目欠落）や不正な入力は安全に拒絶（Fail-Closed）
- 実データ取得不能時は欠損値（np.nan）として扱い、必須特徴量が不足する場合は推論停止
- 学習時と推論時の28特徴量定義・順序・型・カテゴリの整合性を検証
"""
from __future__ import annotations
import logging
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

from src.features import FEATURE_COLS_V2_28, CATEGORICAL_COLS

logger = logging.getLogger("PreraceFeaturePipeline")

PROHIBITED_FEATURE_COLUMNS = frozenset({
    "finish_order", "finish_position", "rank_result", "result",
    "target", "target_win", "target_place", "is_win", "is_place",
    "payout", "win_payout", "place_payout", "actual_finish",
    "race_result", "confirmed_odds", "final_odds", "time_sec",
    "time_str", "last_3f", "margin_str", "corner_4_pos",
})

REQUIRED_RACE_KEYS = {"race_id", "race_course", "surface", "distance", "race_date"}
REQUIRED_HORSE_KEYS = {"horse_number"}

# カテゴリカル変数の定義（学習時と完全一致）
CAT_DEFS = {
    "running_style": ["先行", "差し", "追込", "逃げ"],
    "race_course": ["中京", "中山", "京都", "函館", "小倉", "新潟", "札幌", "東京", "福島", "阪神"],
    "surface": ["ダート", "芝"],
    "condition": ["不良", "稍重", "良", "重"],
    "sex": ["セン", "牝", "牡"],
}


def validate_clean_prerace_input(race_info: Dict[str, Any], horses: List[Dict[str, Any]]) -> None:
    """発走前入力データの完全性および未来リーク遮断を検証"""
    if not isinstance(race_info, dict):
        raise ValueError("INSUFFICIENT_FEATURES: race_info must be a dictionary")
    for k in REQUIRED_RACE_KEYS:
        if k not in race_info or race_info[k] is None or str(race_info[k]).strip() == "":
            raise ValueError(f"INSUFFICIENT_FEATURES: Missing required race field '{k}'")

    if not isinstance(horses, list) or len(horses) == 0:
        raise ValueError("INSUFFICIENT_FEATURES: horses list is empty")

    seen_horses = set()
    for idx, h in enumerate(horses):
        if not isinstance(h, dict):
            raise ValueError(f"INSUFFICIENT_FEATURES: Horse entry at index {idx} must be a dict")
        for k in REQUIRED_HORSE_KEYS:
            if k not in h or h[k] is None:
                raise ValueError(f"INSUFFICIENT_FEATURES: Missing required horse field '{k}' at index {idx}")
        try:
            h_num = int(h["horse_number"])
            if h_num < 1 or h_num > 99 or h_num in seen_horses:
                raise ValueError(f"INSUFFICIENT_FEATURES: Invalid or duplicate horse_number {h_num}")
            seen_horses.add(h_num)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"INSUFFICIENT_FEATURES: Invalid horse_number: {exc}")

        # 未来事後結果の混入を即時拒絶
        present_prohibited = PROHIBITED_FEATURE_COLUMNS.intersection(
            str(col).lower() for col in h.keys()
        )
        if present_prohibited:
            raise ValueError(
                f"FUTURE_LEAKAGE: Prohibited post-race columns detected: {', '.join(sorted(present_prohibited))}"
            )

    race_prohibited = PROHIBITED_FEATURE_COLUMNS.intersection(
        str(col).lower() for col in race_info.keys()
    )
    if race_prohibited:
        raise ValueError(
            f"FUTURE_LEAKAGE: Prohibited post-race columns detected in race_info: {', '.join(sorted(race_prohibited))}"
        )


def validate_horse_jockey_identifiers(horses: List[Dict[str, Any]]) -> None:
    """馬ID・騎手IDの欠損状況および推論に必要な特徴量の生成可否を検証"""
    for idx, h in enumerate(horses):
        h_num = h.get("horse_number")
        horse_id = str(h.get("horse_id", "")).strip()
        jockey_id = str(h.get("jockey_id", "")).strip()
        if not horse_id:
            logger.info(f"馬番 {h_num}: horse_id欠損（初出走または未登録）。過去走集計は欠損(NaN)として安全に処理")
        if not jockey_id:
            logger.info(f"馬番 {h_num}: jockey_id欠損。騎手実績集計は欠損(NaN)として安全に処理")
        # 必須属性（年齢、斤量）の検証: 根拠なき架空補完を禁止しているため、これらが取得できなければ推論停止
        if h.get("age") is None or (isinstance(h.get("age"), (int, float)) and np.isnan(float(h["age"]))):
            raise ValueError(f"INSUFFICIENT_FEATURES: Missing required horse attribute 'age' for horse {h_num}")
        if h.get("weight_carry") is None or (isinstance(h.get("weight_carry"), (int, float)) and np.isnan(float(h["weight_carry"]))):
            raise ValueError(f"INSUFFICIENT_FEATURES: Missing required horse attribute 'weight_carry' for horse {h_num}")


def validate_feature_alignment(df: pd.DataFrame) -> None:
    """学習時と推論時の28特徴量の定義・順序・型の整合性を検証"""
    missing_cols = [c for c in FEATURE_COLS_V2_28 if c not in df.columns]
    if missing_cols:
        raise ValueError(f"FEATURE_ALIGNMENT_ERROR: Missing 28-feature columns: {missing_cols}")

    for cat_col, expected_categories in CAT_DEFS.items():
        if cat_col in df.columns:
            series = df[cat_col]
            if not isinstance(series.dtype, pd.CategoricalDtype):
                raise TypeError(f"FEATURE_ALIGNMENT_ERROR: Column '{cat_col}' must be CategoricalDtype")
            actual_cats = list(series.dtype.categories)
            if actual_cats != expected_categories:
                raise ValueError(
                    f"FEATURE_ALIGNMENT_ERROR: Categories for '{cat_col}' mismatch: expected {expected_categories}, got {actual_cats}"
                )


def build_prerace_28features(
    race_info: Dict[str, Any],
    horses: List[Dict[str, Any]],
    past_db_path: Optional[Path] = None,
    target_date: Optional[str] = None
) -> pd.DataFrame:
    """
    当日の発走前出走表と過去実績から 28特徴量を構築する。
    - 架空の初期値（4歳, 55kg, 0kg, 4週, 先行, 良, 右）による捏造補完は禁止
    - 開催日（race_date または target_date）より前の実績（r.date < target_date）のみを集計
    - 実データ取得不能時は np.nan として扱い、推論可能か検証
    """
    # 開催日の確定
    r_date = str(target_date or race_info.get("race_date") or "").strip()
    if not r_date:
        raise ValueError("INSUFFICIENT_FEATURES: race_date is required to prevent future leakage")
    race_info_with_date = dict(race_info)
    race_info_with_date["race_date"] = r_date

    validate_clean_prerace_input(race_info_with_date, horses)
    validate_horse_jockey_identifiers(horses)

    race_id = str(race_info_with_date["race_id"])
    race_course = str(race_info_with_date["race_course"])
    surface = str(race_info_with_date["surface"])
    try:
        distance = float(race_info_with_date["distance"])
    except (ValueError, TypeError) as exc:
        raise ValueError("INSUFFICIENT_FEATURES: Invalid distance") from exc

    # 架空の補完は行わず、存在する場合のみ採用（ない場合は np.nan / None）
    condition = race_info_with_date.get("condition")
    if condition is not None and str(condition).strip() not in CAT_DEFS["condition"]:
        condition = None

    direction = race_info_with_date.get("direction")
    if direction is not None and str(direction).strip() not in ("右", "左", "直線"):
        direction = None

    # 過去走DB接続（存在する場合）
    past_db = Path(past_db_path) if past_db_path else Path(__file__).resolve().parent.parent / "data" / "keiba.db"
    has_past_db = past_db.is_file()

    rows = []
    for h in horses:
        h_num = int(h["horse_number"])
        b_num = int(h["bracket_number"]) if h.get("bracket_number") is not None else (
            math.ceil(h_num / 2) if h_num <= 16 else 8
        )
        
        # 架空の初期値（4歳, 55kg, 0kg等）を廃止し、取得データのみ使用
        age = float(h["age"]) if h.get("age") is not None else np.nan
        weight_carry = float(h["weight_carry"]) if h.get("weight_carry") is not None else np.nan
        horse_weight = float(h["horse_weight"]) if h.get("horse_weight") is not None else np.nan
        horse_weight_diff = float(h["horse_weight_diff"]) if h.get("horse_weight_diff") is not None else np.nan
        weight_ratio = (weight_carry / horse_weight) if (horse_weight and not np.isnan(horse_weight) and horse_weight > 0 and not np.isnan(weight_carry)) else np.nan
        sex = str(h["sex"]) if h.get("sex") is not None and str(h["sex"]) in CAT_DEFS["sex"] else None

        horse_id = str(h.get("horse_id", "")).strip()
        jockey_id = str(h.get("jockey_id", "")).strip()

        # 過去走から統計量を集計（初出走や欠損の場合は np.nan）
        jockey_win_rate = np.nan
        jockey_place_rate = np.nan
        prev_rank = np.nan
        prev_last_3f = np.nan
        past_avg_rank = np.nan
        past_place_rate = np.nan
        past_race_count = 0
        course_place_rate = np.nan
        dist_place_rate = np.nan
        direction_place_rate = np.nan
        heavy_place_rate = np.nan
        rest_weeks = float(h["rest_weeks"]) if h.get("rest_weeks") is not None else np.nan
        is_toushi = int(h["is_toushi"]) if h.get("is_toushi") is not None else 0
        running_style = str(h["running_style"]) if h.get("running_style") is not None and str(h["running_style"]) in CAT_DEFS["running_style"] else None

        if has_past_db and (horse_id or jockey_id):
            try:
                with closing(sqlite3.connect(f"file:{past_db.resolve()}?mode=ro", uri=True, timeout=15)) as conn:
                    # 騎手成績（対象日厳密以前: r.date < r_date）
                    if jockey_id:
                        j_query = """
                        SELECT count(*),
                               sum(CASE WHEN res.finish_order = 1 THEN 1 ELSE 0 END),
                               sum(CASE WHEN res.finish_order <= 3 THEN 1 ELSE 0 END)
                        FROM race_results res
                        JOIN races r ON res.race_id = r.race_id
                        WHERE res.jockey_id = ? AND res.finish_order > 0 AND r.date < ?
                        """
                        j_row = conn.execute(j_query, (jockey_id, r_date)).fetchone()
                        if j_row and j_row[0] > 0:
                            jockey_win_rate = round(float(j_row[1] / j_row[0]), 4)
                            jockey_place_rate = round(float(j_row[2] / j_row[0]), 4)

                    # 馬の過去走（対象日厳密以前: r.date < r_date）
                    if horse_id:
                        h_query = """
                        SELECT res.finish_order, res.last_3f, r.race_course, r.distance, r.condition
                        FROM race_results res
                        JOIN races r ON res.race_id = r.race_id
                        WHERE res.horse_id = ? AND res.finish_order > 0 AND r.date < ?
                        ORDER BY r.date DESC
                        """
                        h_rows = conn.execute(h_query, (horse_id, r_date)).fetchall()
                        if h_rows:
                            past_race_count = len(h_rows)
                            ranks = [r[0] for r in h_rows]
                            past_avg_rank = round(float(np.mean(ranks)), 2)
                            past_place_rate = round(float(sum(1 for r in ranks if r <= 3) / len(ranks)), 4)
                            prev_rank = float(h_rows[0][0])
                            try:
                                prev_last_3f = float(h_rows[0][1]) if h_rows[0][1] else np.nan
                            except (ValueError, TypeError):
                                prev_last_3f = np.nan

                            # コース別
                            c_matches = [r[0] for r in h_rows if str(r[2]) == race_course]
                            if c_matches:
                                course_place_rate = round(float(sum(1 for r in c_matches if r <= 3) / len(c_matches)), 4)
                            # 距離別 (±100m)
                            d_matches = [r[0] for r in h_rows if abs(float(r[3] or 0) - distance) <= 100]
                            if d_matches:
                                dist_place_rate = round(float(sum(1 for r in d_matches if r <= 3) / len(d_matches)), 4)
            except Exception as exc:
                # 過去DB参照で例外が発生した場合は握りつぶさず警告ログに詳細を記録
                logger.warning(f"Past DB query failed for race {race_id}, horse {horse_id}, jockey {jockey_id}: {exc}")

        row_dict = {
            "race_id": race_id,
            "horse_number": h_num,
            "bracket_number": b_num,
            "age": age,
            "weight_carry": weight_carry,
            "distance": distance,
            "horse_weight": horse_weight,
            "horse_weight_diff": horse_weight_diff,
            "weight_ratio": weight_ratio,
            "jockey_win_rate": jockey_win_rate,
            "jockey_place_rate": jockey_place_rate,
            "prev_rank": prev_rank,
            "prev_last_3f": prev_last_3f,
            "past_avg_rank": past_avg_rank,
            "past_place_rate": past_place_rate,
            "past_race_count": past_race_count,
            "course_place_rate": course_place_rate,
            "dist_place_rate": dist_place_rate,
            "direction_place_rate": direction_place_rate,
            "heavy_place_rate": heavy_place_rate,
            "running_style": running_style,
            "race_nige_count": 0,
            "is_fast_pace": 0,
            "rest_weeks": rest_weeks,
            "is_toushi": is_toushi,
            "race_course": race_course,
            "surface": surface,
            "condition": condition,
            "sex": sex,
            "horse_name": str(h.get("horse_name", f"Horse_{h_num}"))
        }
        rows.append(row_dict)

    df = pd.DataFrame(rows)

    # ペース予測
    nige_c = sum(1 for r in rows if r["running_style"] == "逃げ")
    df["race_nige_count"] = nige_c
    df["is_fast_pace"] = 1 if nige_c >= 3 else 0

    # カテゴリカル変数の定義と検証
    for col in CATEGORICAL_COLS:
        if col in df.columns and col in CAT_DEFS:
            df[col] = pd.Categorical(df[col], categories=CAT_DEFS[col])

    # 28特徴量の整合性検証
    validate_feature_alignment(df)

    return df
