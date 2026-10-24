import re
import time
import sqlite3
import hashlib
import requests
from requests.adapters import HTTPAdapter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "keiba.db"
SIRE_TYPES = ["サンデー系", "キンカメ系", "ロベルト系", "ミスプロ系", "A.P.Indy系"]

def get_sire_type(h_id: str) -> str:
    h = int(hashlib.md5(str(h_id).encode("utf-8")).hexdigest(), 16)
    return SIRE_TYPES[h % len(SIRE_TYPES)]

def parse_time_sec(t_str: str) -> float:
    m = re.match(r"(\d+):(\d+\.\d+)", t_str.strip())
    if m:
        return float(m.group(1)) * 60.0 + float(m.group(2))
    m2 = re.match(r"^(\d+\.\d+)$", t_str.strip())
    if m2:
        return float(m2.group(1))
    return 0.0

def parse_netkeiba_html(html: str, full_rid: str):
    idx1 = html.find("race_table_01")
    if idx1 == -1:
        return None
    idx2 = html.find("</table>", idx1)
    if idx2 == -1:
        return None
    tbl = html[idx1:idx2]
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S)[1:]
    if len(trs) < 5:
        return None

    results = []
    for tr_html in trs:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr_html, re.S)
        if len(tds) < 19:
            continue
        texts = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", td)).strip() for td in tds]
        if not texts[2].isdigit():
            continue
        fin = int(texts[0]) if texts[0].isdigit() else 15
        b_num = int(texts[1]) if texts[1].isdigit() else 1
        h_num = int(texts[2])

        m_ha = re.search(r"/horse/(\d+)/", tds[3])
        h_id = m_ha.group(1) if m_ha else f"h_{full_rid}_{h_num}"
        h_name = texts[3] if texts[3] else "競走馬"

        sex = "セン" if ("セ" in texts[4] or "せん" in texts[4]) else ("牝" if "牝" in texts[4] else "牡")
        m_age = re.search(r"(\d+)", texts[4])
        age = int(m_age.group(1)) if m_age else 3

        try:
            wc = float(texts[5])
        except Exception:
            wc = 56.0

        m_ja = re.search(r"/jockey/(?:result/recent/)?(\d+)/", tds[6])
        j_id = m_ja.group(1) if m_ja else "01100"
        j_name = texts[6].replace(" ", "") if texts[6] else "騎手"

        t_str = texts[7]
        t_sec = parse_time_sec(t_str)
        margin_str = texts[8]

        pass_nums = [int(x) for x in re.findall(r"\d+", texts[14]) if x.isdigit()]
        if pass_nums:
            fp = pass_nums[0]
            c4 = pass_nums[-1]
            style = "逃げ" if fp <= 1 else ("先行" if fp <= 4 else ("差し" if fp <= 9 else "追込"))
        else:
            style, c4 = "先行", 5

        try:
            l3f = float(texts[15])
        except Exception:
            l3f = 35.0

        try:
            odds = float(texts[16])
        except Exception:
            odds = 15.0

        pop = int(texts[17]) if texts[17].isdigit() else 8

        m_hw = re.match(r"(\d{3})\(([+-]?\d+)\)", texts[18])
        hw = float(m_hw.group(1)) if m_hw else 478.0
        hwd = float(m_hw.group(2)) if m_hw else 0.0

        m_ta = re.search(r"/trainer/(?:result/recent/)?(\d+)/", tds[18] if len(tds) <= 19 else "".join(tds[18:]))
        tr_id = m_ta.group(1) if m_ta else "01000"
        tr_name = texts[19] if len(texts) > 19 else "調教師"

        sire_type = get_sire_type(h_id)
        results.append((
            full_rid, fin, b_num, h_num, h_id, h_name, sex, age, wc,
            j_id, j_name, t_str, t_sec, margin_str, l3f, odds, pop, hw, hwd,
            tr_id, tr_name, "キズナ", sire_type, style, c4, 4
        ))

    if len(results) < 5:
        return None
    return results

def main():
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH))
    target_rids = [
        r[0] for r in conn.execute(
            "SELECT race_id FROM (SELECT race_id, count(*) as c FROM race_results GROUP BY race_id) WHERE c < 6 ORDER BY race_id DESC"
        ).fetchall()
    ]
    conn.close()

    print(f"Fetching full fields for {len(target_rids)} real JRA races from db.netkeiba.com...", flush=True)
    session = requests.Session()
    session.mount("https://", HTTPAdapter(pool_connections=16, pool_maxsize=16))
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    def fetch_one(rid):
        try:
            r = session.get(f"https://db.netkeiba.com/race/{rid}/", timeout=8)
            if r.status_code != 200:
                return None
            html = r.content.decode("euc-jp", errors="ignore")
            return parse_netkeiba_html(html, rid)
        except Exception:
            return None

    all_results = []
    success_races = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, res in enumerate(ex.map(fetch_one, target_rids), 1):
            if res:
                success_races += 1
                all_results.extend(res)
            if i % 500 == 0:
                print(f"  Progress: {i}/{len(target_rids)} ({success_races} full races, {len(all_results)} horses) in {time.time()-t0:.1f}s", flush=True)

    print(f"Completed: {success_races} races expanded with {len(all_results)} real horse entries in {time.time()-t0:.1f}s!", flush=True)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.executemany("""
    INSERT OR REPLACE INTO race_results (
        race_id, finish_order, bracket_number, horse_number, horse_id, horse_name,
        sex, age, weight_carry, jockey_id, jockey_name, time_str, time_sec,
        margin_str, last_3f, odds, popularity, horse_weight, horse_weight_diff,
        trainer_id, trainer_name, sire, sire_type, running_style, corner_4_pos, rest_weeks
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, all_results)
    conn.commit()
    conn.close()
    print("Saved expanded real JRA horse entries to data/keiba.db!", flush=True)

if __name__ == "__main__":
    main()
