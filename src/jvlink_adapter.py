"""
JV-Link (JRA-VAN) データ変換アダプター (JV-Link Ingest Adapter)
- JV-Data 公式生レコード（RA: レース詳細, SE: 出走馬, O1: 単複オッズ）を厳格デコード
- CP932の「文字数」と「バイト数」の混同を完全解消:
  全角文字（馬名等）によるオフセットずれを防止するため、必ず raw_bytes からバイト単位でスライスしてデコード
- レースID衝突の完全防止: 16桁完全レースキー (YYYYMMDD + 場2 + 回2 + 日2 + R2) を一意の race_id として採用
- 複数レースの同時取得・変換に対応: レースキーごとに RA, SE, O1 を完全に分離・グルーピング
- レース間での出走馬・馬番・オッズの混入を物理的に完全防止
- 架空データの推測補完（0101やデフォルト値）は完全禁止 (Fail-Closed)
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
    "10": ("芝", "直線"), "11": ("芝", "左"), "12": ("芝", "左"),
    "13": ("芝", "左"), "14": ("芝", "左"), "15": ("芝", "左"),
    "16": ("芝", "左"), "17": ("芝", "右"), "18": ("芝", "右"),
    "19": ("芝", "右"), "20": ("芝", "右"), "21": ("芝", "右"),
    "22": ("芝", "右"), "23": ("ダート", "左"), "24": ("ダート", "右"),
    "25": ("ダート", "左"), "26": ("ダート", "右"),
    "27": ("ダート", "左"), "28": ("ダート", "右"),
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

# 本番利用未検証のJV-Dataレコード種別
UNVERIFIED_RECORD_TYPES: Set[str] = {
    "O2", "O3", "O4", "O5", "O6", "O7", "HR", "JG", "WH"
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
    return None


def parse_jv_ra_record(raw_bytes: bytes) -> Dict[str, Any]:
    """
    RA レース詳細レコードのパース (Shift-JIS 固定長バイトスライス)
    ※ 文字数ではなく厳密なバイト位置 (raw_bytes[start:end]) で切り出す。
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise TypeError(f"Expected bytes, got {type(raw_bytes).__name__}")
    if len(raw_bytes) != 1272:
        raise ValueError(f"RA record too short: length {len(raw_bytes)} != 1272 required")

    rec_type = raw_bytes[0:2].decode("ascii", errors="replace")
    if raw_bytes[2:3] not in (b"1", b"2"):
        raise ValueError("RA record is not pre-race (DataKubun 1 or 2)")
    if rec_type != "RA":
        raise ValueError(f"Expected RA record, got '{rec_type}'")

    # 16桁レースキー: 年(4), 月日(4), 場コード(2), 回(2), 日(2), R(2)
    race_key = raw_bytes[11:27].decode("ascii", errors="replace")
    year = raw_bytes[11:15].decode("ascii", errors="replace").strip()
    month_day = raw_bytes[15:19].decode("ascii", errors="replace").strip()
    course_code = raw_bytes[19:21].decode("ascii", errors="replace").strip()
    kai = raw_bytes[21:23].decode("ascii", errors="replace").strip()
    nichi = raw_bytes[23:25].decode("ascii", errors="replace").strip()
    race_num_str = raw_bytes[25:27].decode("ascii", errors="replace").strip()

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
    full_race_id = race_key
    legacy_race_id = f"{year}{course_code}{race_num_str.zfill(2)}"

    # 発走時刻 (JV-Data spec: 1-based 874-877)
    post_time_str = raw_bytes[873:877].decode("ascii", errors="replace").strip()
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

    # 距離(698..701) とトラック(706..707): 1-based byte positions
    track_code = raw_bytes[705:707].decode("ascii", errors="replace").strip()
    dist_str = raw_bytes[697:701].decode("ascii", errors="replace").strip()

    if track_code not in JRA_TRACK_MAP:
        raise ValueError(f"Invalid or unverified track code in RA record: '{track_code}'")
    if not (dist_str.isdigit() and 800 <= int(dist_str) <= 4000):
        raise ValueError(f"Invalid or missing distance in RA record: '{dist_str}'")

    distance = float(dist_str)
    surface, direction = JRA_TRACK_MAP[track_code]

    # 馬場状態は速報成績以前の RA1/RA2 で未設定 (0) が正常
    cond_pos = 888 if surface == "芝" else 889
    cond_code = raw_bytes[cond_pos:cond_pos+1].decode("ascii", errors="replace").strip()
    if cond_code not in JRA_CONDITION_MAP and cond_code not in ("0", " "):
        raise ValueError(f"Invalid condition code for {surface}: '{cond_code}'")
    condition = JRA_CONDITION_MAP.get(cond_code)

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
    SE 馬毎出走情報レコードのパース (Shift-JIS 固定長バイトスライス)
    ※ 馬名が全角（日本語）であってもバイト位置（raw_bytes）からスライスするため、
       後続の性別コード・馬齢・斤量のオフセットが絶対にずれない。
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise TypeError(f"Expected bytes, got {type(raw_bytes).__name__}")
    if len(raw_bytes) != 555:
        raise ValueError(f"SE record too short: length {len(raw_bytes)} != 555 required")

    rec_type = raw_bytes[0:2].decode("ascii", errors="replace")
    if raw_bytes[2:3] != b"2":
        raise ValueError("SE horse numbers unavailable before DataKubun 2 or race already finished")
    if rec_type != "SE":
        raise ValueError(f"Expected SE record, got '{rec_type}'")

    race_key = raw_bytes[11:27].decode("ascii", errors="replace")

    # 枠番 (27:28), 馬番 (28:30)
    b_num_str = raw_bytes[27:28].decode("ascii", errors="replace").strip()
    h_num_str = raw_bytes[28:30].decode("ascii", errors="replace").strip()

    if not (b_num_str.isdigit() and 1 <= int(b_num_str) <= 8):
        raise ValueError(f"Invalid bracket number in SE record: '{b_num_str}'")
    if not (h_num_str.isdigit() and 1 <= int(h_num_str) <= 28):
        raise ValueError(f"Invalid horse number in SE record: '{h_num_str}'")

    b_num = int(b_num_str)
    h_num = int(h_num_str)

    # 血統登録番号 (30:40, 10桁)
    horse_id = raw_bytes[30:40].decode("ascii", errors="replace").strip()
    if not (horse_id.isdigit() and len(horse_id) == 10):
        raise ValueError(f"Invalid or missing horse_id in SE record: '{horse_id}'")

    # 馬名 (40:76, 36バイト Shift-JIS)
    horse_name = raw_bytes[40:76].decode("cp932", errors="strict").strip()
    if not horse_name:
        raise ValueError("Missing horse name in SE record")

    # 性別コード (76:77), 馬齢 (77:79) - バイト位置で確実に取得
    sex_code = raw_bytes[78:79].decode("ascii", errors="replace").strip()
    age_str = raw_bytes[82:84].decode("ascii", errors="replace").strip()

    if sex_code not in JRA_SEX_MAP:
        raise ValueError(f"Invalid sex code in SE record: '{sex_code}'")
    if not (age_str.isdigit() and 2 <= int(age_str) <= 15):
        raise ValueError(f"Invalid age in SE record: '{age_str}'")

    sex = JRA_SEX_MAP[sex_code]
    age = float(age_str)

    # 負担重量 (81:84, 0.1kg単位, 例: 560 -> 56.0kg)
    weight_carry_str = raw_bytes[288:291].decode("ascii", errors="replace").strip()
    if not (weight_carry_str.isdigit() and 400 <= int(weight_carry_str) <= 700):
        raise ValueError(f"Invalid weight carry in SE record: '{weight_carry_str}'")
    weight_carry = float(weight_carry_str) / 10.0

    # 騎手コード (84:89, 5桁)
    jockey_id = raw_bytes[296:301].decode("ascii", errors="replace").strip()
    if not (jockey_id.isdigit() and len(jockey_id) == 5):
        raise ValueError(f"Invalid jockey_id in SE record: '{jockey_id}'")

    # 馬体重と増減 (325..330, one-based; unknown pre-race values remain None)
    h_weight = None
    h_weight_diff = None
    if len(raw_bytes) >= 331:
        hw_str = raw_bytes[324:327].decode("ascii", errors="replace").strip()
        hwd_str = raw_bytes[328:331].decode("ascii", errors="replace").strip()
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
    """Decode O1 win odds per official JV-Data 4.9.0.1, section 7, page 12.

    Positions in that specification are 1-based *bytes*:
    race key 12..27, announcement MMDDhhmm 28..35,
    win odds (horse no 2 + odds 4 + rank 2) 44..267, 28 entries.
    Record length is 962 including CRLF. Never interpret sentinel odds
    such as 9999 (capped), 0000 (no bets), ----, **** as actual prices.
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise TypeError("O1 must be raw bytes")
    if len(raw_bytes) != 962 or raw_bytes[:2] != b"O1":
        raise ValueError("Invalid O1 record type or length (962 bytes expected)")
    if raw_bytes[-2:] != b"\r\n":
        raise ValueError("O1 record terminator is invalid")

    race_key = raw_bytes[11:27].decode("ascii")
    if len(race_key) != 16 or not race_key.isascii() or not race_key.isdigit():
        raise ValueError("O1 race key must be 16 ASCII digits")
    data_kubun = raw_bytes[2:3].decode("ascii")
    if data_kubun not in ("1", "2", "3"):
        raise ValueError("O1 is not a supported pre-race odds record")
    creation_date = raw_bytes[3:11].decode("ascii")
    if len(creation_date) != 8 or not creation_date.isascii() or not creation_date.isdigit():
        raise ValueError("O1 creation date invalid")
    try:
        created = datetime.strptime(creation_date, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("O1 creation date impossible") from exc

    obs_iso = None
    if data_kubun == "1":
        mmddhhmm = raw_bytes[27:35].decode("ascii")
        if len(mmddhhmm) != 8 or not mmddhhmm.isascii() or not mmddhhmm.isdigit():
            raise ValueError("O1 intermediate odds require a valid MMDDhhmm")
        # O1 has no announcement year. Resolve it against the record's
        # creation date, including Dec/Jan transitions, and refuse ambiguity.
        candidates = []
        for year in (created.year - 1, created.year, created.year + 1):
            try:
                candidate = datetime.strptime(str(year) + mmddhhmm, "%Y%m%d%H%M")
                candidates.append(candidate)
            except ValueError:
                continue
        if not candidates:
            raise ValueError("O1 announcement timestamp is not a calendar date")
        observed = min(candidates, key=lambda dt: abs((created - dt).total_seconds()))
        if abs((created - observed).total_seconds()) > 7 * 86400:
            raise ValueError("O1 announcement year ambiguous versus creation date")
        obs_iso = observed.strftime("%Y-%m-%dT%H:%M:00+09:00")

    sale_flag = raw_bytes[39:40].decode("ascii")
    if sale_flag not in ("0", "1", "3", "7"):
        raise ValueError("O1 invalid win ticket sale flag")
    odds_map: Dict[int, Tuple[float, Optional[str]]] = {}
    if sale_flag == "7":
        for index in range(28):
            start = 43 + index * 8
            slot = raw_bytes[start:start + 8]
            horse_text = slot[:2].decode("ascii")
            odd_text = slot[2:6].decode("ascii")
            if horse_text == "  ":
                if slot.strip():
                    raise ValueError("O1 vacant horse slot has non-space data")
                continue
            if not horse_text.isascii() or not horse_text.isdigit():
                raise ValueError(f"O1 invalid horse-number slot {index + 1}")
            horse = int(horse_text)
            if horse != index + 1:
                raise ValueError(f"O1 horse number is out of order: {horse}")
            if odd_text in ("9999", "0000", "----", "****", "    "):
                continue
            if not odd_text.isascii() or not odd_text.isdigit():
                raise ValueError(f"O1 invalid odds field for horse {horse}")
            odds = int(odd_text) / 10.0
            if not (1.0 <= odds < 999.9):
                raise ValueError(f"O1 odds outside representable range: {odds}")
            odds_map[horse] = (odds, obs_iso)

    return {"race_key": race_key, "odds_map": odds_map}


def convert_multiple_jvlink_records_to_feed(records: List[bytes]) -> Dict[str, Any]:
    """
    JV-Linkの生レコードリスト (複数レースの RA, SE, O1) から、
    発走前28特徴量パイプラインへ投入可能な認可フィード辞書を構築。
    
    【レース間分離の保証】
    - レースキー (16桁) ごとに RA, SE, O1 を完全分離してグルーピング。
    - 他レースの馬やオッズが混ざることを完全に排除。
    """
    ra_by_key: Dict[str, Dict[str, Any]] = {}
    se_by_key: Dict[str, Dict[int, Dict[str, Any]]] = {}
    ra_version_by_key: Dict[str, str] = {}
    odds_by_key: Dict[str, Dict[int, Tuple[float, Optional[str]]]] = {}

    for raw in records:
        if not isinstance(raw, (bytes, bytearray)) or len(raw) < 2:
            continue
        rtype = raw[:2].decode("ascii", errors="ignore")

        if rtype in UNVERIFIED_RECORD_TYPES:
            continue

        if rtype == "RA":
            parsed_ra = parse_jv_ra_record(raw)
            rkey = parsed_ra["race_key"]
            kubun = raw[2:3].decode("ascii")
            # Per JRA-VAN, records are not guaranteed sorted. A final pre-race
            # DataKubun 2 header supersedes a preliminary DataKubun 1 header.
            previous = ra_version_by_key.get(rkey)
            if previous is None or kubun >= previous:
                ra_by_key[rkey] = parsed_ra
                ra_version_by_key[rkey] = kubun

        elif rtype == "SE":
            parsed_se = parse_jv_se_record(raw)
            rkey = parsed_se["race_key"]
            h_num = parsed_se["horse_number"]
            # A later record for the same horse replaces an earlier correction.
            se_by_key.setdefault(rkey, {})[h_num] = parsed_se

        elif rtype == "O1":
            parsed_o1 = parse_jv_o1_record(raw)
            rkey = parsed_o1["race_key"]
            entries = odds_by_key.setdefault(rkey, {})
            for horse_num, quote in parsed_o1["odds_map"].items():
                previous = entries.get(horse_num)
                if previous is None or (quote[1] is not None and
                                        (previous[1] is None or quote[1] >= previous[1])):
                    entries[horse_num] = quote

    if not ra_by_key:
        raise ValueError("No valid RA (race header) record found in JV-Data")

    # RAヘッダーが存在しない孤立SE/O1レコードの混入を物理的に完全遮断 (Fail-Closed)
    for se_rkey in se_by_key.keys():
        if se_rkey not in ra_by_key:
            raise ValueError(f"Race key mismatch in SE record: race key '{se_rkey}' has no corresponding RA header")
    for o1_rkey in odds_by_key.keys():
        if o1_rkey not in ra_by_key:
            raise ValueError(f"Race key mismatch in O1 record: race key '{o1_rkey}' has no corresponding RA header")

    processed_races = []
    # レースキー昇順で整列
    for rkey in sorted(ra_by_key.keys()):
        ra = ra_by_key[rkey]
        horses = list(se_by_key.get(rkey, {}).values())
        odds_map = odds_by_key.get(rkey, {})

        if not horses:
            # 出走馬が1頭もいないレースは除外
            continue

        # オッズ紐付け
        for h in horses:
            h_num = h["horse_number"]
            if h_num in odds_map:
                h["win_odds"] = odds_map[h_num][0]
                h["odds_observed_at"] = odds_map[h_num][1]
            else:
                h["win_odds"] = None
                h["odds_observed_at"] = None

        horses.sort(key=lambda x: x["horse_number"])
        ra["horses"] = horses
        processed_races.append(ra)

    if not processed_races:
        raise ValueError("No races with valid runner records could be constructed")

    return {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "source": "jvlink",
        "certified_source": False,
        "races": processed_races
    }


def convert_jvlink_records_to_prerace_feed(records: List[bytes]) -> Dict[str, Any]:
    """単一または複数レースの生レコードからフィード辞書を構築（後方互換用）"""
    return convert_multiple_jvlink_records_to_feed(records)


def convert_jvlink_batch_to_prerace_feed(records: List[bytes]) -> Dict[str, Any]:
    """複数レースの生レコードバッチからフィード辞書を構築"""
    return convert_multiple_jvlink_records_to_feed(records)
