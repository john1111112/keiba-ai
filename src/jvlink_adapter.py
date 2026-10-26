"""
JV-Link (JRA-VAN) データ変換アダプター (JV-Link Ingest Adapter)
- JV-Data 公式生レコード（RA: レース詳細, SE: 出走馬, O1: 単複オッズ）を厳格デコード
- 架空データの推測補完（4歳、55kg、1600m、東京、良、牡等）は完全禁止
- 必須情報が欠損・不正な場合は推測補完せず、Fail-Closed で例外を送出し処理停止
- レースID衝突の完全防止: 16桁完全レースキー (YYYYMMDD + 場2 + 回2 + 日2 + R2) を一意の race_id として採用
- 旧8桁レースIDとの後方互換マッピングをサポート
- レースキー・馬番・オッズの整合性を完全検証し、別レースの混入を物理的に遮断
- 未検証レコード（馬連・ワイド・3連単等）は本番利用禁止
"""
from __future__ import annotations
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set

# JRA公式競馬場コード (01〜10)
JRA_COURSE_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉"
}

# JRA公式トラックコード (芝・ダート・障害 / 回り)
JRA_TRACK_MAP = {
    "10": ("芝", "右"), "11": ("芝", "左"), "12": ("芝", "直線"),
    "13": ("芝", "右"), "14": ("芝", "左"), "15": ("芝", "右"),
    "16": ("芝", "左"), "17": ("芝", "右"), "18": ("芝", "左"),
    "19": ("芝", "直線"), "20": ("芝", "右"), "21": ("芝", "左"),
    "22": ("芝", "右"),
    "23": ("ダート", "右"), "24": ("ダート", "左"), "25": ("ダート", "右"),
    "26": ("ダート", "左"), "27": ("ダート", "右"), "28": ("ダート", "左"),
    "29": ("ダート", "直線")
}

# JRA公式馬場状態コード (1:良, 2:稍重, 3:重, 4:不良)
JRA_CONDITION_MAP = {
    "1": "良", "2": "稍重", "3": "重", "4": "不良"
}

# JRA公式性別コード (1:牡, 2:牝, 3:セン)
JRA_SEX_MAP = {
    "1": "牡", "2": "牝", "3": "セン"
}

# 本番利用未検証のJV-Dataレコード種別（これらは本番推論への利用を禁止）
UNVERIFIED_RECORD_TYPES: Set[str] = {
    "O2",  # 枠連オッズ
    "O3",  # 馬連オッズ
    "O4",  # ワイドオッズ
    "O5",  # 馬単オッズ
    "O6",  # 3連複オッズ
    "O7",  # 3連単オッズ
    "HR",  # 払戻
    "JG",  # 競走馬除外
    "WH"   # 馬体重
}


def normalize_to_16char_race_key(race_id_or_key: str) -> Optional[str]:
    """
    16桁公式レースキー (YYYYMMDD + 場2 + 回2 + 日2 + R2) の検証と正規化。
    ※ 開催回・開催日を推測（'0101'等）で補完することは完全禁止。
       16桁完全キーでない場合、自動補完せず None（未解決）を返す (Fail-Closed)。
    """
    s = str(race_id_or_key).strip()
    if len(s) == 16 and s.isdigit():
        return s
    # 開催回・開催日を含まない旧8桁等は推測補完せず未解決
    return None


def parse_jv_ra_record(raw_bytes: bytes) -> Dict[str, Any]:
    """
    RA レース詳細レコードのパース (Shift-JIS 固定長)
    【一意性保証】
    - race_id に16桁公式完全レースキー (YYYYMMDD + 場コード2 + 回2 + 日2 + R2) を採用し、
      異なる開催日でのID衝突を完全に防止。
    - 旧8桁ID (legacy_race_id) も併せて保持し後方互換性を維持。
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise TypeError(f"Expected bytes, got {type(raw_bytes).__name__}")
    if len(raw_bytes) < 350:
        raise ValueError(f"RA record too short: length {len(raw_bytes)} < 350 required")

    text = raw_bytes.decode("cp932", errors="replace")
    rec_type = text[0:2]
    if rec_type != "RA":
        raise ValueError(f"Expected RA record, got '{rec_type}'")

    # 16桁レースキー: 年(4), 月日(4), 場コード(2), 回(2), 日(2), R(2)
    race_key = text[11:27]
    year = text[11:15].strip()
    month_day = text[15:19].strip()
    course_code = text[19:21].strip()
    kai = text[21:23].strip()
    nichi = text[23:25].strip()
    race_num_str = text[25:27].strip()

    if not (year.isdigit() and len(year) == 4):
        raise ValueError(f"Invalid or missing year in RA record: '{year}'")
    if not (month_day.isdigit() and len(month_day) == 4):
        raise ValueError(f"Invalid or missing month_day in RA record: '{month_day}'")
    if course_code not in JRA_COURSE_MAP:
        raise ValueError(f"Invalid or unverified course code in RA record: '{course_code}'")
    if not (kai.isdigit() and len(kai) == 2):
        raise ValueError(f"Invalid or missing kai in RA record: '{kai}'")
    if not (nichi.isdigit() and len(nichi) == 2):
        raise ValueError(f"Invalid or missing nichi in RA record: '{nichi}'")
    if not (race_num_str.isdigit() and 1 <= int(race_num_str) <= 12):
        raise ValueError(f"Invalid race number in RA record: '{race_num_str}'")

    race_course = JRA_COURSE_MAP[course_code]
    race_num = int(race_num_str)

    # 衝突しない完全16桁一意ID
    full_race_id = race_key
    legacy_race_id = f"{year}{course_code}{race_num_str.zfill(2)}"

    # 発走時刻 (HHMM: 344-348)
    post_time_str = text[344:348].strip()
    if not (post_time_str.isdigit() and len(post_time_str) == 4):
        raise ValueError(f"Missing or invalid post time in RA record: '{post_time_str}'")
    hh = int(post_time_str[:2])
    mm = int(post_time_str[2:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Out of range post time: {hh:02d}:{mm:02d}")

    month = int(month_day[:2])
    day = int(month_day[2:])
    try:
        dt_val = datetime(int(year), month, day, hh, mm)
    except ValueError as exc:
        raise ValueError(f"Invalid calendar date/time in RA record: {exc}") from exc

    date_iso = f"{year}-{month_day[:2]}-{month_day[2:]}"
    race_time_iso = f"{date_iso}T{post_time_str[:2]}:{post_time_str[2:]}:00+09:00"

    # 距離とトラック (271-277)
    track_code = text[271:273].strip()
    dist_str = text[273:277].strip()

    if track_code not in JRA_TRACK_MAP:
        raise ValueError(f"Invalid or unverified track code in RA record: '{track_code}'")
    if not (dist_str.isdigit() and 800 <= int(dist_str) <= 4000):
        raise ValueError(f"Invalid or missing distance in RA record: '{dist_str}'")

    distance = float(dist_str)
    surface, direction = JRA_TRACK_MAP[track_code]

    # 馬場状態 (348:349 芝馬場、349:350 ダート馬場)
    cond_pos = 348 if surface == "芝" else 349
    cond_code = text[cond_pos:cond_pos+1].strip()
    if cond_code not in JRA_CONDITION_MAP:
        raise ValueError(f"Invalid or unverified condition code for {surface}: '{cond_code}'")
    condition = JRA_CONDITION_MAP[cond_code]

    return {
        "race_key": race_key,
        "race_id": full_race_id,
        "legacy_race_id": legacy_race_id,
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
    """
    SE 馬毎出走情報レコードのパース (Shift-JIS 固定長)
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise TypeError(f"Expected bytes, got {type(raw_bytes).__name__}")
    if len(raw_bytes) < 90:
        raise ValueError(f"SE record too short: length {len(raw_bytes)} < 90 required")

    text = raw_bytes.decode("cp932", errors="replace")
    rec_type = text[0:2]
    if rec_type != "SE":
        raise ValueError(f"Expected SE record, got '{rec_type}'")

    race_key = text[11:27]

    # 枠番 (27:28), 馬番 (28:30)
    b_num_str = text[27:28].strip()
    h_num_str = text[28:30].strip()

    if not (b_num_str.isdigit() and 1 <= int(b_num_str) <= 8):
        raise ValueError(f"Invalid bracket number in SE record: '{b_num_str}'")
    if not (h_num_str.isdigit() and 1 <= int(h_num_str) <= 28):
        raise ValueError(f"Invalid horse number in SE record: '{h_num_str}'")

    b_num = int(b_num_str)
    h_num = int(h_num_str)

    # 血統登録番号 (30:40, 10桁), 馬名 (40:76)
    horse_id = text[30:40].strip()
    horse_name = text[40:76].strip()

    if not (horse_id.isdigit() and len(horse_id) == 10):
        raise ValueError(f"Invalid or missing horse_id in SE record: '{horse_id}'")
    if not horse_name:
        raise ValueError("Missing horse name in SE record")

    # 性別コード (76:77), 馬齢 (77:79)
    sex_code = text[76:77].strip()
    age_str = text[77:79].strip()

    if sex_code not in JRA_SEX_MAP:
        raise ValueError(f"Invalid sex code in SE record: '{sex_code}'")
    if not (age_str.isdigit() and 2 <= int(age_str) <= 15):
        raise ValueError(f"Invalid age in SE record: '{age_str}'")

    sex = JRA_SEX_MAP[sex_code]
    age = float(age_str)

    # 負担重量 (81:84, 0.1kg単位, 例: 560 -> 56.0kg)
    weight_carry_str = text[81:84].strip()
    if not (weight_carry_str.isdigit() and 400 <= int(weight_carry_str) <= 700):
        raise ValueError(f"Invalid weight carry in SE record: '{weight_carry_str}'")
    weight_carry = float(weight_carry_str) / 10.0

    # 騎手コード (84:89, 5桁)
    jockey_id = text[84:89].strip()
    if not (jockey_id.isdigit() and len(jockey_id) == 5):
        raise ValueError(f"Invalid jockey_id in SE record: '{jockey_id}'")

    # 馬体重情報（発表済みの場合は後続バイト 168:174。未発表時は None で可）
    h_weight = None
    h_weight_diff = None
    if len(text) >= 174:
        hw_str = text[168:171].strip()
        hwd_str = text[171:174].strip()
        if hw_str.isdigit() and 300 <= int(hw_str) <= 650:
            h_weight = float(hw_str)
        if hwd_str.replace("+", "").replace("-", "").isdigit():
            try:
                h_weight_diff = float(hwd_str)
            except ValueError:
                h_weight_diff = None

    return {
        "race_key": race_key,
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


def parse_jv_o1_record(raw_bytes: bytes) -> Dict[str, Any]:
    """
    O1 単複オッズレコードのパース (Shift-JIS 固定長)
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise TypeError(f"Expected bytes, got {type(raw_bytes).__name__}")
    if len(raw_bytes) < 47:
        raise ValueError(f"O1 record too short: length {len(raw_bytes)} < 47 required")

    text = raw_bytes.decode("cp932", errors="replace")
    rec_type = text[0:2]
    if rec_type != "O1":
        raise ValueError(f"Expected O1 record, got '{rec_type}'")

    race_key = text[11:27]

    # 発表月日時分: 11-23 (YYYYMMDDHHMM)
    obs_time_str = text[11:23].strip()
    if not (obs_time_str.isdigit() and len(obs_time_str) == 12):
        raise ValueError(f"Invalid observation timestamp in O1 record: '{obs_time_str}'")

    y = obs_time_str[:4]
    m = obs_time_str[4:6]
    d = obs_time_str[6:8]
    hh = obs_time_str[8:10]
    mm = obs_time_str[10:12]
    obs_iso = f"{y}-{m}-{d}T{hh}:{mm}:00+09:00"

    odds_map: Dict[int, Tuple[float, str]] = {}
    offset = 47
    for num in range(1, 29):
        if offset + 4 <= len(text):
            o_str = text[offset:offset+4].strip()
            if o_str.isdigit() and 10 <= int(o_str) <= 9990:
                odds_map[num] = (float(o_str) / 10.0, obs_iso)
        offset += 8

    return {
        "race_key": race_key,
        "odds_map": odds_map
    }


def convert_jvlink_batch_to_prerace_feed(records: List[bytes]) -> Dict[str, Any]:
    """Convert a multi-race JV-Data batch without mixing horses or odds across races.

    Each race is processed by the existing strict single-race converter.
    Any orphan or malformed record rejects the entire batch.
    """
    grouped: Dict[str, List[bytes]] = {}
    ra_keys: Set[str] = set()
    for raw in records:
        if not isinstance(raw, (bytes, bytearray)) or len(raw) < 27:
            raise ValueError("Invalid JV-Data record")
        kind = bytes(raw[:2]).decode("ascii", errors="ignore")
        if kind not in {"RA", "SE", "O1"}:
            continue
        # Key occupies the ASCII-only header region; do not decode full CP932 text
        key = bytes(raw[11:27]).decode("ascii", errors="strict")
        if len(key) != 16 or not key.isdigit():
            raise ValueError(f"Invalid race key in {kind} record")
        if kind == "RA":
            if key in ra_keys:
                raise ValueError(f"Duplicate RA for race {key}")
            ra_keys.add(key)
        grouped.setdefault(key, []).append(bytes(raw))
    if not grouped or not ra_keys:
        raise ValueError("No RA records found")
    if set(grouped) != ra_keys:
        raise ValueError(f"Orphan race records: {sorted(set(grouped) - ra_keys)}")
    races = []
    for key in sorted(grouped):
        race_feed = convert_jvlink_records_to_prerace_feed(grouped[key])
        races.extend(race_feed["races"])
    return {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "source": "jvlink",
        "certified_source": False,
        "races": races,
    }


def convert_jvlink_records_to_prerace_feed(records: List[bytes]) -> Dict[str, Any]:
    """
    JV-Link生レコードリスト (RA, SE, O1) から、
    発走前28特徴量パイプラインへ投入可能な認可フィード辞書を構築。
    """
    ra_info = None
    target_race_key = None
    horses = []
    seen_horse_numbers = set()
    odds_map = {}

    for raw in records:
        if not isinstance(raw, (bytes, bytearray)) or len(raw) < 2:
            continue
        rtype = raw[:2].decode("ascii", errors="ignore")

        if rtype in UNVERIFIED_RECORD_TYPES:
            continue

        if rtype == "RA":
            if ra_info is not None:
                raise ValueError("Multiple RA records in single-race batch is forbidden")
            parsed_ra = parse_jv_ra_record(raw)
            target_race_key = parsed_ra["race_key"]
            ra_info = parsed_ra

        elif rtype == "SE":
            parsed_se = parse_jv_se_record(raw)
            if target_race_key is not None and parsed_se["race_key"] != target_race_key:
                raise ValueError(
                    f"Race key mismatch in SE record: expected '{target_race_key}', got '{parsed_se['race_key']}'"
                )
            h_num = parsed_se["horse_number"]
            if h_num in seen_horse_numbers:
                raise ValueError(f"Duplicate horse number {h_num} in SE records")
            seen_horse_numbers.add(h_num)
            horses.append(parsed_se)

        elif rtype == "O1":
            parsed_o1 = parse_jv_o1_record(raw)
            if target_race_key is not None and parsed_o1["race_key"] != target_race_key:
                raise ValueError(
                    f"Race key mismatch in O1 record: expected '{target_race_key}', got '{parsed_o1['race_key']}'"
                )
            odds_map.update(parsed_o1["odds_map"])

    if not ra_info:
        raise ValueError("No valid RA (race header) record found in JV-Data")
    if not horses:
        raise ValueError("No valid SE (runner) records found in JV-Data")

    # オッズの紐付け
    for h in horses:
        h_num = h["horse_number"]
        if h_num in odds_map:
            h["win_odds"] = odds_map[h_num][0]
            h["odds_observed_at"] = odds_map[h_num][1]
        else:
            h["win_odds"] = None
            h["odds_observed_at"] = None

    horses.sort(key=lambda x: x["horse_number"])
    ra_info["horses"] = horses

    return {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "source": "jvlink",
        "certified_source": False,
        "races": [ra_info]
    }
