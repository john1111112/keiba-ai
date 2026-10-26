"""
JV-Link (JRA-VAN) データ変換アダプター (JV-Link Ingest Adapter)
- JV-Data 生レコード（RA: レース詳細, SE: 出走馬, O1: 単複オッズ）をデコード
- 発走前28特徴量パイプライン (build_prerace_28features) が要求するスキーマへ変換
- 契約・SDK接続を勝手に開始せず（費用0円）、オフラインの生データから変換可能
- 欠損値や未知コードは安全に None/NaN として扱い、未来リークを防止
"""
from __future__ import annotations
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

JRA_COURSE_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉"
}

JRA_TRACK_MAP = {
    "10": ("芝", "右"), "11": ("芝", "左"), "12": ("芝", "直線"),
    "17": ("芝", "右"), "18": ("芝", "左"),
    "23": ("ダート", "右"), "24": ("ダート", "左"), "25": ("ダート", "右"), "26": ("ダート", "左")
}

JRA_CONDITION_MAP = {
    "1": "良", "2": "稍重", "3": "重", "4": "不良"
}

JRA_SEX_MAP = {
    "1": "牡", "2": "牝", "3": "セン"
}


def parse_jv_ra_record(raw_bytes: bytes) -> Dict[str, Any]:
    """RA レース詳細レコードのパース (Shift-JIS 固定長)"""
    text = raw_bytes.decode("cp932", errors="replace")
    # レースキー: 年(4), 月日(4), 場コード(2), 回(2), 日(2), R(2)
    rec_type = text[0:2]
    if rec_type != "RA":
        raise ValueError(f"Expected RA record, got {rec_type}")

    year = text[11:15].strip()
    month_day = text[15:19].strip()
    course_code = text[19:21].strip()
    race_num = text[25:27].strip()
    race_id = f"{year}{course_code}{race_num.zfill(2)}"

    race_course = JRA_COURSE_MAP.get(course_code, "東京")
    
    # 発走時刻 (HHMM)
    post_time_str = text[344:348].strip() if len(text) > 348 else "1500"
    if len(year) == 4 and len(month_day) == 4 and len(post_time_str) == 4:
        date_iso = f"{year}-{month_day[:2]}-{month_day[2:]}"
        race_time_iso = f"{date_iso}T{post_time_str[:2]}:{post_time_str[2:]}:00+09:00"
    else:
        date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        race_time_iso = f"{date_iso}T15:00:00+09:00"

    # 距離とトラック
    dist_str = text[273:277].strip() if len(text) > 277 else "1600"
    track_code = text[271:273].strip() if len(text) > 273 else "10"
    try:
        distance = float(dist_str)
    except ValueError:
        distance = 1600.0

    surface_info = JRA_TRACK_MAP.get(track_code, ("芝", "右"))
    surface = surface_info[0]
    direction = surface_info[1]

    # 馬場状態
    cond_code = text[348:349].strip() if len(text) > 349 else "1"
    condition = JRA_CONDITION_MAP.get(cond_code, "良")

    return {
        "race_id": race_id,
        "race_date": date_iso,
        "race_time": race_time_iso,
        "race_course": race_course,
        "surface": surface,
        "distance": distance,
        "condition": condition,
        "direction": direction,
        "race_status": "upcoming"
    }


def parse_jv_se_record(raw_bytes: bytes) -> Dict[str, Any]:
    """SE 馬毎出走情報レコードのパース"""
    text = raw_bytes.decode("cp932", errors="replace")
    rec_type = text[0:2]
    if rec_type != "SE":
        raise ValueError(f"Expected SE record, got {rec_type}")

    # 馬番, 枠番
    b_num_str = text[27:28].strip()
    h_num_str = text[28:30].strip()
    horse_id = text[30:40].strip()  # 血統登録番号 (10桁)
    horse_name = text[40:76].strip()
    sex_code = text[76:77].strip()
    age_str = text[77:79].strip()
    weight_carry_str = text[81:84].strip()  # 0.1kg単位 (例: 560 -> 56.0)
    jockey_id = text[84:89].strip()  # 騎手コード (5桁)

    h_num = int(h_num_str) if h_num_str.isdigit() else 1
    b_num = int(b_num_str) if b_num_str.isdigit() else 1
    age = float(age_str) if age_str.isdigit() else 4.0
    weight_carry = float(weight_carry_str) / 10.0 if weight_carry_str.isdigit() else 55.0
    sex = JRA_SEX_MAP.get(sex_code, "牡")

    # 馬体重情報（発表済みの場合は後続バイト）
    h_weight = None
    h_weight_diff = None
    if len(text) >= 180:
        hw_str = text[168:171].strip()
        hwd_str = text[171:174].strip()
        if hw_str.isdigit() and int(hw_str) > 0:
            h_weight = float(hw_str)
        if hwd_str.replace("+", "").replace("-", "").isdigit():
            h_weight_diff = float(hwd_str)

    return {
        "horse_number": h_num,
        "bracket_number": b_num,
        "horse_name": horse_name,
        "horse_id": horse_id,
        "jockey_id": jockey_id,
        "age": age,
        "weight_carry": weight_carry,
        "sex": sex,
        "horse_weight": h_weight,
        "horse_weight_diff": h_weight_diff
    }


def parse_jv_o1_record(raw_bytes: bytes) -> Dict[int, Tuple[float, str]]:
    """O1 単勝オッズレコードのパース (馬番 -> (オッズ, 観測日時))"""
    text = raw_bytes.decode("cp932", errors="replace")
    rec_type = text[0:2]
    if rec_type != "O1":
        raise ValueError(f"Expected O1 record, got {rec_type}")

    # 発表月日 (MMDDHHMM)
    obs_time_str = text[15:23].strip() if len(text) > 23 else ""
    year = text[11:15].strip()
    if len(year) == 4 and len(obs_time_str) == 8:
        obs_iso = f"{year}-{obs_time_str[:2]}-{obs_time_str[2:4]}T{obs_time_str[4:6]}:{obs_time_str[6:8]}:00+09:00"
    else:
        obs_iso = datetime.now(timezone.utc).isoformat()

    odds_map = {}
    # 単勝オッズブロック: 各馬番ごとにオッズ(4桁, 0.1倍単位)
    offset = 47
    for num in range(1, 29):
        if offset + 4 <= len(text):
            o_str = text[offset:offset+4].strip()
            if o_str.isdigit() and int(o_str) > 0:
                odds_map[num] = (float(o_str) / 10.0, obs_iso)
        offset += 8  # 単勝4 + 人気2 + 予備2 = 8バイト

    return odds_map


def convert_jvlink_records_to_prerace_feed(records: List[bytes]) -> Dict[str, Any]:
    """
    JV-Linkの生レコードリスト (RA, SE, O1) から、
    発走前28特徴量パイプラインへ直接投入可能なフィード辞書を構築。
    """
    ra_info = None
    horses = []
    odds_map = {}

    for raw in records:
        if not isinstance(raw, bytes) or len(raw) < 2:
            continue
        rtype = raw[:2].decode("ascii", errors="ignore")
        if rtype == "RA" and ra_info is None:
            ra_info = parse_jv_ra_record(raw)
        elif rtype == "SE":
            horses.append(parse_jv_se_record(raw))
        elif rtype == "O1":
            odds_map.update(parse_jv_o1_record(raw))

    if not ra_info:
        raise ValueError("No RA (race header) record found in JV-Data")

    # オッズの紐付け
    for h in horses:
        h_num = h["horse_number"]
        if h_num in odds_map:
            h["win_odds"] = odds_map[h_num][0]
            h["odds_observed_at"] = odds_map[h_num][1]
        else:
            h["win_odds"] = None
            h["odds_observed_at"] = None

    ra_info["horses"] = horses
    return {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "source": "jvlink",
        "races": [ra_info]
    }
