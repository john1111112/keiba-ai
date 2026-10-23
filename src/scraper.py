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

        distance = int(dist_match.group(1)) if dist_match else 1600

        direction = "右" if "右" in detail_text else ("左" if "左" in detail_text else "直線")

        

        weather_match = re.search(r"天候\s*:\s*([^\s/]+)", detail_text)

        weather = weather_match.group(1) if weather_match else "晴"

        

        condition_match = re.search(r"(?:芝|ダート)\s*:\s*([^\s/]+)", detail_text)

        condition = condition_match.group(1) if condition_match else "良"

        

        small_text = rdata.find("p", class_="smalltxt").get_text() if rdata.find("p", class_="smalltxt") else ""

        date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", small_text)

        if date_match:

            date_str = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"

        else:

            date_str = "2024-01-01"

            

        course_match = re.search(r"\d+回([^\d]+)\d+日", small_text)

        race_course = course_match.group(1) if course_match else "東京"

        

        r_num_match = re.search(r"(\d+)R", race_name) or re.search(r"(\d{1,2})R", small_text)

        race_number = int(r_num_match.group(1)) if r_num_match else 11

        

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

                    horse_id = re.search(r"/horse/(\w+)", horse_link["href"]).group(1) if horse_link and "href" in horse_link.attrs else f"h_{horse_name}"

                    

                    sex_age = cols[4].get_text(strip=True)

                    sex = sex_age[0] if sex_age else "牡"

                    age = int(sex_age[1:]) if len(sex_age) > 1 and sex_age[1:].isdigit() else 3

                    weight_carry = float(cols[5].get_text(strip=True)) if cols[5].get_text(strip=True) else 55.0

                    

                    jockey_link = cols[6].find("a")

                    jockey_name = jockey_link.get_text(strip=True) if jockey_link else cols[6].get_text(strip=True)

                    jockey_id = re.search(r"/jockey/(\w+)", jockey_link["href"]).group(1) if jockey_link and "href" in jockey_link.attrs else f"j_{jockey_name}"

                    

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

                    horse_weight = float(hw_match.group(1)) if hw_match and hw_match.group(1) else 480.0

                    horse_weight_diff = float(hw_match.group(2)) if hw_match and hw_match.group(2) else 0.0

                    

                    trainer_link = cols[18].find("a") if len(cols) > 18 else None

                    trainer_name = trainer_link.get_text(strip=True) if trainer_link else (cols[18].get_text(strip=True) if len(cols) > 18 else "調教師")

                    trainer_id = re.search(r"/trainer/(\w+)", trainer_link["href"]).group(1) if trainer_link and "href" in trainer_link.attrs else f"t_{trainer_name}"

                    

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

                        "popularity": 1

                    })

                    

        return race_info, results, payouts

    except Exception as e:

        print(f"Error fetching race {race_id}: {e}")

        return None, [], []



def bulk_generate_large_historical_dataset(target_races: int = 3000, progress_callback=None) -> int:

    """

    数千〜数万レース規模の大規模レースデータ（JRA過去3年分相当）を高速バルク生成し、

    ローカルDBに一気に集積する。

    """

    init_db()

    conn = get_connection()

    cur = conn.cursor()

    

    # 既存レース数

    cur.execute("SELECT COUNT(*) FROM races")

    existing_count = cur.fetchone()[0]

    

    courses = [

        ("東京", "左"), ("中山", "右"), ("京都", "右"), ("阪神", "右"),

        ("中京", "左"), ("新潟", "左"), ("小倉", "右"), ("福島", "右"),

        ("札幌", "右"), ("函館", "右")

    ]

    surfaces = ["芝", "芝", "芝", "ダート", "ダート"]

    distances = [1200, 1400, 1600, 1800, 2000, 2200, 2400, 2500]

    conditions = ["良", "良", "良", "稍重", "重", "不良"]

    

    # 実在のトップ騎手25名

    jockeys = [

        ("j_01", "ルメール", 0.25, 0.47), ("j_02", "川田将雅", 0.23, 0.46),

        ("j_03", "武豊", 0.14, 0.35), ("j_04", "横山武史", 0.13, 0.34),

        ("j_05", "坂井瑠星", 0.13, 0.33), ("j_06", "戸崎圭太", 0.12, 0.33),

        ("j_07", "松山弘平", 0.11, 0.31), ("j_08", "岩田望来", 0.10, 0.29),

        ("j_09", "鮫島克駿", 0.08, 0.26), ("j_10", "デムーロ", 0.10, 0.28),

        ("j_11", "西村淳也", 0.09, 0.27), ("j_12", "菅原明良", 0.08, 0.25),

        ("j_13", "田辺裕信", 0.07, 0.24), ("j_14", "三浦皇成", 0.07, 0.23),

        ("j_15", "藤岡佑介", 0.06, 0.21), ("j_16", "丹内祐次", 0.07, 0.22),

        ("j_17", "津村明秀", 0.07, 0.22), ("j_18", "菱田裕二", 0.06, 0.20),

        ("j_19", "佐々木大輔", 0.08, 0.24), ("j_20", "モレイラ", 0.28, 0.52),

        ("j_21", "和田竜二", 0.06, 0.20), ("j_22", "横山和生", 0.08, 0.25),

        ("j_23", "北村友一", 0.07, 0.23), ("j_24", "吉田隼人", 0.06, 0.20),

        ("j_25", "団野大成", 0.07, 0.22)

    ]

    

    # 調教師

    trainers = [

        "矢作芳人", "国枝栄", "木村哲也", "中内田充", "友道康夫",

        "手塚貴久", "杉山晴紀", "堀宣行", "池江泰寿", "藤原英昭",

        "音無秀孝", "須貝尚介", "高野友和", "清水久詞", "斎藤崇史"

    ]

    

    # 登録馬プール（2,500頭の大規模母集団）

    import datetime

    start_date = datetime.date(2021, 1, 5)

    

    horse_pool = [

        (f"h_{i:05d}", f"競走馬_{i:04d}", random.choice(["牡", "牝", "セン"]), random.choice([3, 4, 5, 6]), round(random.gauss(50.0, 12.0), 1))

        for i in range(1, 2501)

    ]

    

    batch_races = []

    batch_results = []

    batch_payouts = []

    

    inserted = 0

    for r_idx in range(target_races):

        total_idx = existing_count + r_idx

        # 年間約3400レース、週末開催のシミュレート

        days_offset = (total_idx // 24) * 7 + ((total_idx % 24) // 12)

        r_date = start_date + datetime.timedelta(days=days_offset)

        course, direction = random.choice(courses)

        surface = random.choice(surfaces)

        dist = random.choice(distances)

        cond = random.choice(conditions)

        race_id = f"{r_date.year:04d}{total_idx:07d}"

        

        num_horses = random.randint(12, 18)

        race_horses = random.sample(horse_pool, num_horses)

        

        horse_scores = []

        for h_id, h_name, sex, age, base_ability in race_horses:

            jock = random.choice(jockeys)

            trainer = random.choice(trainers)

            j_skill = jock[2] * 35

            w_carry = 57.0 if sex == "牡" else 55.0

            if age == 3:

                w_carry -= 1.0

            luck = random.gauss(0, 4.5)

            score = base_ability + j_skill + luck - (w_carry - 55.0) * 0.6

            horse_scores.append((score, h_id, h_name, sex, age, w_carry, jock, trainer))

            

        horse_scores.sort(key=lambda x: x[0], reverse=True)

        

        grade = "G1" if (r_idx % 120 == 0) else ("G2" if (r_idx % 60 == 0) else ("G3" if (r_idx % 30 == 0) else ("特別" if (r_idx % 12 >= 9) else "一般")))

        r_name = f"{course}{dist}m {grade}"

        

        batch_races.append((

            race_id, r_name, r_date.isoformat(), course, (total_idx % 12) + 1,

            surface, dist, direction, "晴" if cond == "良" else "曇", cond, grade

        ))

        

        horse_order = list(range(1, num_horses + 1))

        random.shuffle(horse_order)

        

        winner_num = 0

        winner_odds = 1.0

        winner_pop = 1

        

        for rank, (sc, h_id, h_name, sex, age, w_carry, jock, trainer) in enumerate(horse_scores, start=1):

            h_num = horse_order[rank - 1]

            bracket = min(8, (h_num - 1) // 2 + 1)

            

            pop_noise = rank + random.randint(-2, 2)

            pop = max(1, min(num_horses, pop_noise))

            odds = round(max(1.2, 1.7 ** (pop * 0.48) + random.uniform(-0.4, 0.8)), 1)

            

            if rank == 1:

                winner_num = h_num

                winner_odds = odds

                winner_pop = pop

                

            base_time = (dist / 1000.0) * 58.5

            finish_time_sec = round(base_time + (rank * 0.22) + random.uniform(-0.1, 0.15), 2)

            m = int(finish_time_sec // 60)

            s = finish_time_sec % 60

            time_str = f"{m}:{s:04.1f}"

            

            l3f = round(33.2 + (rank * 0.28) + random.uniform(-0.2, 0.4), 1)

            hw = round(random.uniform(440, 530))

            hwd = random.choice([-8, -6, -4, -2, 0, 0, 0, 2, 4, 6, 8])

            

            batch_results.append((

                race_id, rank, bracket, h_num, h_id, h_name,

                sex, age, w_carry, jock[0], jock[1],

                time_str, finish_time_sec, "1/2" if rank > 1 else "",

                l3f, odds, pop, hw, hwd, f"t_{trainer}", trainer

            ))

            

        batch_payouts.append((

            race_id, "単勝", str(winner_num), int(winner_odds * 100), winner_pop

        ))

        

        inserted += 1

        

        # 500レースごとにバルクコミット

        if len(batch_races) >= 500:

            cur.executemany("""

            INSERT OR REPLACE INTO races (

                race_id, race_name, date, race_course, race_number,

                surface, distance, direction, weather, condition, grade

            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            """, batch_races)

            

            cur.executemany("""

            INSERT OR REPLACE INTO race_results (

                race_id, finish_order, bracket_number, horse_number,

                horse_id, horse_name, sex, age, weight_carry,

                jockey_id, jockey_name, time_str, time_sec, margin_str,

                last_3f, odds, popularity, horse_weight, horse_weight_diff,

                trainer_id, trainer_name

            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            """, batch_results)

            

            cur.executemany("""

            INSERT INTO payouts (race_id, bet_type, combination, payout, popularity)

            VALUES (?, ?, ?, ?, ?)

            """, batch_payouts)

            

            conn.commit()

            batch_races.clear()

            batch_results.clear()

            batch_payouts.clear()

            

            if progress_callback:

                progress_callback(inserted, target_races)



    if batch_races:

        cur.executemany("""

        INSERT OR REPLACE INTO races (

            race_id, race_name, date, race_course, race_number,

            surface, distance, direction, weather, condition, grade

        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        """, batch_races)

        cur.executemany("""

        INSERT OR REPLACE INTO race_results (

            race_id, finish_order, bracket_number, horse_number,

            horse_id, horse_name, sex, age, weight_carry,

            jockey_id, jockey_name, time_str, time_sec, margin_str,

            last_3f, odds, popularity, horse_weight, horse_weight_diff,

            trainer_id, trainer_name

        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        """, batch_results)

        cur.executemany("""

        INSERT INTO payouts (race_id, bet_type, combination, payout, popularity)

        VALUES (?, ?, ?, ?, ?)

        """, batch_payouts)

        conn.commit()



    conn.close()

    return inserted





def generate_sample_historical_data(num_races: int = 100) -> int:

    """互換用ラッパー関数"""

    return bulk_generate_large_historical_dataset(target_races=num_races)

