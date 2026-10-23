import sqlite3
import random
import pandas as pd
from pathlib import Path

random.seed(42)

db_path = Path(__file__).resolve().parent.parent / "data" / "keiba.db"
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

# 1. 過去確定レース (date <= '2026-10-23' かつ finish_order >= 1) から実在履歴を持つ馬を取得
hist_df = pd.read_sql_query("""
    SELECT 
        res.horse_id, res.horse_name, res.sex, res.age,
        res.sire, res.sire_type, res.running_style,
        COUNT(*) as cnt,
        AVG(CASE WHEN res.finish_order <= 3 THEN 1.0 ELSE 0.0 END) as place_rate,
        AVG(res.finish_order) as avg_rank
    FROM race_results res
    JOIN races r ON res.race_id = r.race_id
    WHERE r.date <= '2026-10-23' AND res.finish_order >= 1
    GROUP BY res.horse_id
    HAVING cnt >= 4
    ORDER BY place_rate DESC, avg_rank ASC
""", conn)

print(f"Found {len(hist_df)} historical horses with >= 4 races.")

# 馬を強さティア別に分類
elite_horses = hist_df[hist_df["place_rate"] >= 0.55].to_dict("records")
good_horses = hist_df[(hist_df["place_rate"] >= 0.35) & (hist_df["place_rate"] < 0.55)].to_dict("records")
normal_horses = hist_df[hist_df["place_rate"] < 0.35].to_dict("records")

print(f"Elite: {len(elite_horses)}, Good: {len(good_horses)}, Normal: {len(normal_horses)}")

# カタカナ馬名プール（出馬表で見栄えが良くリアリティのある実在風馬名）
katakana_prefixes = [
    "サトノ", "ダノン", "ロード", "メイショウ", "ショウナン", "ジャスティン", "コスモ",
    "シン", "アーバン", "エコロ", "サンライズ", "ウイン", "マイネル", "レッド",
    "カフェ", "ヤマニン", "トウカイ", "マテンロウ", "テーオー", "タガノ", "スマート", "ナムラ"
]
katakana_suffixes = [
    "グランツ", "デサイル", "デルレイ", "タバル", "ラプンタ", "ミラノ", "キュランダ",
    "エンペラー", "シック", "ヴァルツ", "ジパング", "スティール", "アイランド", "ディーヴァ",
    "ヴェーグ", "エレーデ", "ナイフ", "コンチェルト", "シーフ", "ヴィング", "オペラ", "オリエンス",
    "ノヴァ", "ブレイズ", "クラウン", "インパクト", "レジェンド", "ストーム", "スパーク", "アロー"
]
generated_names = []
for p in katakana_prefixes:
    for s in katakana_suffixes:
        generated_names.append(p + s)

# 既存の週末レース (2026-10-24, 2026-10-25) を一旦クリアしてクリーンに再作成
cur.execute("DELETE FROM race_results WHERE race_id IN (SELECT race_id FROM races WHERE date IN ('2026-10-24', '2026-10-25'))")
cur.execute("DELETE FROM races WHERE date IN ('2026-10-24', '2026-10-25')")

jockeys_top = [
    ("j_01", "ルメール"), ("j_02", "川田将雅"), ("j_20", "モレイラ"), ("j_03", "武豊")
]
jockeys_mid = [
    ("j_04", "横山武史"), ("j_05", "坂井瑠星"), ("j_06", "戸崎圭太"), ("j_07", "松山弘平"),
    ("j_08", "岩田望来"), ("j_09", "鮫島克駿"), ("j_10", "デムーロ"), ("j_11", "西村淳也"),
    ("j_12", "菅原明良"), ("j_13", "田辺裕信"), ("j_14", "三浦皇成"), ("j_15", "藤岡佑介")
]

courses = ["京都", "東京", "新潟"]
dates = ["2026-10-24", "2026-10-25"]

new_races = []
new_results = []

# 菊花賞の特別出走馬名
kikkasho_names = [
    "ダノンデサイル", "アーバンシック", "コスモキュランダ", "メイショウタバル",
    "ショウナンラプンタ", "ヘデントール", "ビザンチンドリーム", "エコロヴァルツ",
    "アドマイヤテラ", "メリオーレム", "ピースワンデュック", "ウエストナウ",
    "シュバルツクーゲル", "アレグロブリランテ", "ハヤテノフクノスケ", "ノーブルスカイ"
]

race_seq = 0
for d in dates:
    clean_d = d.replace("-", "")
    for c in courses:
        for r_num in range(1, 13):
            race_seq += 1
            rid = f"{clean_d}_{c}_{r_num:02d}"
            surf = "芝" if r_num >= 9 or (r_num % 2 == 1) else "ダート"
            dist = random.choice([1200, 1400, 1600, 1800, 2000, 2400])
            grade = "一般"
            r_name = f"{c}{r_num}R {surf}{dist}m"
            
            if rid == "20261025_京都_11":
                surf = "芝"
                dist = 3000
                grade = "G1"
                r_name = "菊花賞 (G1)"
            elif rid == "20261025_東京_11":
                surf = "ダート"
                dist = 2100
                grade = "特別"
                r_name = "ブラジルカップ (L)"
            elif rid == "20261024_東京_11":
                surf = "芝"
                dist = 1600
                grade = "G2"
                r_name = "富士ステークス (G2)"
            elif rid == "20261025_京都_02":
                surf = "ダート"
                dist = 1400
                r_name = "京都2R ダート1400m"

            direction = "右" if c == "京都" else "左"
            new_races.append((rid, r_name, d, c, r_num, surf, dist, direction, "晴", "良", grade))

            # レースのターゲットタイプを決定（SSが過剰にならないよう現実的な割合に制御）
            # 36レース中: SS狙い ~3レース、S狙い ~6レース、A狙い ~9レース、B/見送り ~18レース
            if rid in ("20261025_京都_11", "20261024_東京_11", "20261025_東京_10", "20261024_京都_10"):
                race_type = "SS"  # 厳選された高勝率×15倍以上レース
            elif r_num in (11, 12) or (r_num == 9 and c == "京都"):
                race_type = "S"   # 手堅い高勝率Sランク
            elif r_num in (7, 8, 10):
                race_type = "A"   # Aランク
            else:
                race_type = "B_OR_SKIP"  # 一般戦（1R〜6R等）は混戦Bランクまたは見送り

            num_h = 16
            if race_type == "SS":
                # 1頭だけ超エリート馬＋トップ騎手を配置し、オッズを15.5〜19.8倍の美味しい盲点オッズに設定
                h1 = random.choice(elite_horses[:40])
                others = random.sample(normal_horses[:300], num_h - 1)
                sampled = [h1] + others
            elif race_type == "S":
                # 1頭だけ超エリート馬＋トップ騎手を配置し、オッズを2.4〜5.8倍の本命オッズに設定
                h1 = random.choice(elite_horses[:40])
                others = random.sample(normal_horses[:300], num_h - 1)
                sampled = [h1] + others
            elif race_type == "A":
                # 準エリート馬を配置
                h1 = random.choice(good_horses[:80])
                others = random.sample(normal_horses[:300], num_h - 1)
                sampled = [h1] + others
            else:
                # 混戦（実力が拮抗した一般馬同士）
                sampled = random.sample(normal_horses[:400], num_h)

            # 馬番をシャッフル
            horse_nums = list(range(1, num_h + 1))
            random.shuffle(horse_nums)

            for idx, h_rec in enumerate(sampled):
                h_num = horse_nums[idx]
                bracket = min(8, (h_num - 1) // 2 + 1)
                h_id = h_rec["horse_id"]
                if rid == "20261025_京都_11":
                    h_name = kikkasho_names[idx]
                else:
                    # horse_id に基づいて一貫したカタカナ馬名を割り当て
                    hid_num = int(str(h_id).replace("h_", "")) if str(h_id).startswith("h_") and str(h_id)[2:].isdigit() else (idx + race_seq * 17)
                    h_name = generated_names[hid_num % len(generated_names)]

                sex = h_rec["sex"] if pd.notna(h_rec["sex"]) else "牡"
                age = int(h_rec["age"]) if pd.notna(h_rec["age"]) else 3
                sire = h_rec["sire"] if pd.notna(h_rec["sire"]) else "キズナ"
                sire_type = h_rec["sire_type"] if pd.notna(h_rec["sire_type"]) else "芝中長距離"
                run_style = h_rec["running_style"] if pd.notna(h_rec["running_style"]) else "先行"

                if idx == 0 and race_type == "SS":
                    j_id, j_name = random.choice(jockeys_top)
                    odds = round(random.uniform(15.4, 21.5), 1)
                    pop = random.randint(5, 7)
                    rest_w = 4
                elif idx == 0 and race_type == "S":
                    j_id, j_name = random.choice(jockeys_top)
                    odds = round(random.uniform(2.5, 5.2), 1)
                    pop = 1
                    rest_w = 4
                elif idx == 0 and race_type == "A":
                    j_id, j_name = random.choice(jockeys_top)
                    odds = round(random.uniform(3.2, 6.8), 1)
                    pop = 2
                    rest_w = 5
                else:
                    j_id, j_name = random.choice(jockeys_mid)
                    pop = min(16, idx + 2)
                    if race_type == "B_OR_SKIP":
                        # 混戦または低期待値オッズ
                        odds = round(max(1.8, 2.1 + idx * 1.8 + random.uniform(-0.3, 0.8)), 1)
                    else:
                        odds = round(max(2.2, 3.0 + idx * 2.5 + random.uniform(-0.5, 1.5)), 1)
                    rest_w = random.choice([2, 3, 4, 6, 8, 10])

                new_results.append((
                    rid, 0, bracket, h_num, h_id, h_name, sex, age, 56.0,
                    j_id, j_name, "", 0.0, "", None, odds, pop, 480, 0,
                    "t_01", "友道康夫", sire, sire_type, run_style, 5, rest_w
                ))

cur.executemany("""
INSERT OR REPLACE INTO races (
    race_id, race_name, date, race_course, race_number,
    surface, distance, direction, weather, condition, grade
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", new_races)

cur.executemany("""
INSERT OR REPLACE INTO race_results (
    race_id, finish_order, bracket_number, horse_number, horse_id, horse_name,
    sex, age, weight_carry, jockey_id, jockey_name, time_str, time_sec,
    margin_str, last_3f, odds, popularity, horse_weight, horse_weight_diff,
    trainer_id, trainer_name, sire, sire_type, running_style, corner_4_pos, rest_weeks
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", new_results)

conn.commit()
conn.close()
print(f"Successfully populated {len(new_races)} weekend races ({len(new_results)} entries) with real historical horse IDs!")
