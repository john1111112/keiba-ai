import re

import time

import random

import requests

from bs4 import BeautifulSoup

from typing import Dict, List, Any, Optional, Tuple

from pathlib import Path

from .database import save_race, init_db, get_connection



HEADERS = {

    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

}



def parse_time_to_sec(time_str: str) -> Optional[float]:

    if not time_str or not isinstance(time_str, str):

        return None

    time_str = time_str.strip()

    try:

        if ":" in time_str:

            parts = time_str.split(":")

            m = float(parts[0])

            s = float(parts[1])

            return round(m * 60 + s, 2)

        else:

            return round(float(time_str), 2)

    except Exception:

        return None



def fetch_netkeiba_race(race_id: str, delay_sec: float = 1.0) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:

    """

    netkeibaの過去レース結果ページ(db.netkeiba.com/race/{race_id})からデータを取得・解析

    """

    url = f"https://db.netkeiba.com/race/{race_id}/"

    time.sleep(delay_sec + random.uniform(0.1, 0.4))

    

    try:

        resp = requests.get(url, headers=HEADERS, timeout=10)

        resp.encoding = "EUC-JP"

        if resp.status_code != 200:

            return None, [], []

            

        soup = BeautifulSoup(resp.text, "lxml")

        rdata = soup.find("div", class_="data_intro")

        if not rdata:

            return None, [], []

            

        race_name = rdata.find("h1").get_text(strip=True) if rdata.find("h1") else "Unknown"

        detail_p = rdata.find("p")

        detail_text = detail_p.get_text() if detail_p else ""

        

        surface = "芝" if "芝" in detail_text else ("ダート" if "ダ" in detail_text else "障害")

        dist_match = re.search(r"(\d{3,4})m", detail_text)

        distance = int(dist_match.group(1)) if dist_match else None

        direction = "右" if "右" in detail_text else ("左" if "左" in detail_text else "直線")

        

        weather_match = re.search(r"天候\s*:\s*([^\s/]+)", detail_text)

        weather = weather_match.group(1) if weather_match else None

        

        condition_match = re.search(r"(?:芝|ダート)\s*:\s*([^\s/]+)", detail_text)

        condition = condition_match.group(1) if condition_match else None

        

        small_text = rdata.find("p", class_="smalltxt").get_text() if rdata.find("p", class_="smalltxt") else ""

        date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", small_text)

        if date_match:

            date_str = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"

        else:

            return None, [], []  # Unknown date invalidates historical race identity

            

        course_match = re.search(r"\d+回([^\d]+)\d+日", small_text)

        race_course = course_match.group(1) if course_match else None

        

        r_num_match = re.search(r"(\d+)R", race_name) or re.search(r"(\d{1,2})R", small_text)

        race_number = int(r_num_match.group(1)) if r_num_match else None

        

        race_info = {

            "race_id": race_id,

            "race_name": race_name,

            "date": date_str,

            "race_course": race_course,

            "race_number": race_number,

            "surface": surface,

            "distance": distance,

            "direction": direction,

            "weather": weather,

            "condition": condition,

            "grade": "G1" if "GI" in race_name else ("G2" if "GII" in race_name else ("G3" if "GIII" in race_name else "一般"))

        }

        

        results = []

        table = soup.find("table", class_="race_table_01")

        if table:

            rows = table.find_all("tr")[1:]

            for row in rows:

                cols = row.find_all("td")

                if len(cols) < 15:

                    continue

                try:

                    finish_order_str = cols[0].get_text(strip=True)

                    finish_order = int(finish_order_str) if finish_order_str.isdigit() else 99

                    bracket_num = int(cols[1].get_text(strip=True)) if cols[1].get_text(strip=True).isdigit() else 0

                    horse_num = int(cols[2].get_text(strip=True)) if cols[2].get_text(strip=True).isdigit() else 0

                    

                    horse_link = cols[3].find("a")

                    horse_name = horse_link.get_text(strip=True) if horse_link else cols[3].get_text(strip=True)

                    horse_id = re.search(r"/horse/(\w+)", horse_link["href"]).group(1) if horse_link and "href" in horse_link.attrs else None

                    

                    sex_age = cols[4].get_text(strip=True)

                    sex = sex_age[0] if sex_age else None

                    age = int(sex_age[1:]) if len(sex_age) > 1 and sex_age[1:].isdigit() else None

                    weight_carry = float(cols[5].get_text(strip=True)) if cols[5].get_text(strip=True) else None

                    

                    jockey_link = cols[6].find("a")

                    jockey_name = jockey_link.get_text(strip=True) if jockey_link else cols[6].get_text(strip=True)

                    jockey_id = re.search(r"/jockey/(\w+)", jockey_link["href"]).group(1) if jockey_link and "href" in jockey_link.attrs else None

                    

                    time_str = cols[7].get_text(strip=True)

                    time_sec = parse_time_to_sec(time_str)

                    margin_str = cols[8].get_text(strip=True)

                    

                    last_3f_str = cols[11].get_text(strip=True)

                    last_3f = float(last_3f_str) if last_3f_str and last_3f_str.replace(".", "", 1).isdigit() else None

                    

                    odds_str = cols[12].get_text(strip=True)

                    odds = float(odds_str) if odds_str and odds_str.replace(".", "", 1).isdigit() else 0.0

                    

                    pop_str = cols[13].get_text(strip=True)

                    popularity = int(pop_str) if pop_str.isdigit() else 0

                    

                    weight_str = cols[14].get_text(strip=True)

                    hw_match = re.search(r"(\d+)(?:\(([-+]?\d+)\))?", weight_str)

                    horse_weight = float(hw_match.group(1)) if hw_match and hw_match.group(1) else None

                    horse_weight_diff = float(hw_match.group(2)) if hw_match and hw_match.group(2) else None

                    

                    trainer_link = cols[18].find("a") if len(cols) > 18 else None

                    trainer_name = trainer_link.get_text(strip=True) if trainer_link else (cols[18].get_text(strip=True) if len(cols) > 18 else None)

                    trainer_id = re.search(r"/trainer/(\w+)", trainer_link["href"]).group(1) if trainer_link and "href" in trainer_link.attrs else None

                    

                    results.append({

                        "finish_order": finish_order,

                        "bracket_number": bracket_num,

                        "horse_number": horse_num,

                        "horse_id": horse_id,

                        "horse_name": horse_name,

                        "sex": sex,

                        "age": age,

                        "weight_carry": weight_carry,

                        "jockey_id": jockey_id,

                        "jockey_name": jockey_name,

                        "time_str": time_str,

                        "time_sec": time_sec,

                        "margin_str": margin_str,

                        "last_3f": last_3f,

                        "odds": odds,

                        "popularity": popularity,

                        "horse_weight": horse_weight,

                        "horse_weight_diff": horse_weight_diff,

                        "trainer_id": trainer_id,

                        "trainer_name": trainer_name

                    })

                except Exception:

                    continue



        payouts = []

        pay_tables = soup.find_all("table", class_="pay_table_01")

        for ptab in pay_tables:

            for prow in ptab.find_all("tr"):

                th = prow.find("th")

                tds = prow.find_all("td")

                if th and len(tds) >= 2:

                    btype = th.get_text(strip=True)

                    comb = tds[0].get_text(strip=True)

                    pay_str = tds[1].get_text(strip=True).replace(",", "").replace("円", "")

                    payout = int(pay_str) if pay_str.isdigit() else 0

                    payouts.append({

                        "bet_type": btype,

                        "combination": comb,

                        "payout": payout,

                        "popularity": None  # Cannot infer payout popularity from an unranked table

                    })

                    

        return race_info, results, payouts

    except Exception as e:

        print(f"Error fetching race {race_id}: {e}")

        return None, [], []



def bulk_generate_large_historical_dataset(target_races: int = 3000, progress_callback=None) -> int:
    """Disabled: simulated races/horses/odds must never enter the real training DB."""
    raise RuntimeError(
        "SIMULATED_DATA_GENERATION_DISABLED: fake races, horse identities, "
        "odds and payouts are forbidden in the KEIBA AI production database."
    )


def generate_sample_historical_data(num_races: int = 100) -> int:
    """Backward-compatible API that fails closed instead of producing fake races."""
    raise RuntimeError(
        "SIMULATED_DATA_GENERATION_DISABLED: use a separate explicitly isolated "
        "test fixture database; production database cannot accept synthetic races."
    )
