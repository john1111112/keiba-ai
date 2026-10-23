import sqlite3
import random

conn = sqlite3.connect('data/keiba.db')
cur = conn.cursor()

courses = ['京都', '東京', '新潟']
dates = ['2026-10-24', '2026-10-25']

horses_pool = [
    'サトノグランツ', 'タスティエーラ', 'ソールオリエンス', 'ドゥレッツァ', 'ベラジオオペラ',
    'ロードデルレイ', 'レーベンスティール', 'リバティアイランド', 'マスクトディーヴァ',
    'ブレイディヴェーグ', 'ドゥラエレーデ', 'トップナイフ', 'ハーツコンチェルト', 'ファントムシーフ',
    'スキルヴィング', 'ショウナンラプンタ', 'メイショウタバル', 'ジャスティンミラノ', 'コスモキュランダ',
    'シンエンペラー', 'アーバンシック', 'ダノンデサイル', 'エコロヴァルツ', 'サンライズジパング'
]

jockeys = [
    ('j_01', 'ルメール'), ('j_02', '川田将雅'), ('j_03', '武豊'), ('j_04', 'モレイラ'),
    ('j_05', '坂井瑠星'), ('j_06', '戸崎圭太'), ('j_07', '横山武史'), ('j_08', '松山弘平')
]

new_races = []
new_results = []

for d in dates:
    clean_d = d.replace('-', '')
    for c in courses:
        for r_num in range(1, 13):
            rid = f'{clean_d}_{c}_{r_num:02d}'
            if rid == '20261025_京都_11':
                continue
            cur.execute('SELECT count(*) FROM races WHERE race_id = ?', (rid,))
            if cur.fetchone()[0] > 0:
                continue
            surf = '芝' if r_num >= 9 or (r_num % 2 == 1) else 'ダート'
            dist = random.choice([1200, 1400, 1600, 1800, 2000, 2400])
            r_name = f'{c}{r_num}R {surf}{dist}m'
            if r_num == 11 and c == '東京' and d == '2026-10-25':
                r_name = 'ブラジルカップ (L)'
            elif r_num == 11 and c == '東京' and d == '2026-10-24':
                r_name = '富士ステークス (G2)'
            new_races.append((rid, r_name, d, c, r_num, surf, dist, '右' if c != '東京' else '左', '晴', '良', 'G1' if 'G' in r_name else '一般'))
            
            num_h = 16
            for h_idx in range(num_h):
                h_num = h_idx + 1
                bracket = min(8, (h_num - 1) // 2 + 1)
                h_name = horses_pool[h_idx % len(horses_pool)]
                h_id = f'h_{h_name}_{clean_d}'
                j_id, j_name = random.choice(jockeys)
                odds = round(max(1.8, 1.8 ** (h_num * 0.45) + random.uniform(-0.4, 0.6)), 1)
                pop = h_num
                new_results.append((
                    rid, 0, bracket, h_num, h_id, h_name, '牡', 3, 56.0,
                    j_id, j_name, '', 0.0, '', None, odds, pop, 480, 0, 't_01', '国枝栄'
                ))

cur.executemany('''
INSERT OR REPLACE INTO races (race_id, race_name, date, race_course, race_number, surface, distance, direction, weather, condition, grade)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
''', new_races)

cur.executemany('''
INSERT OR REPLACE INTO race_results (race_id, finish_order, bracket_number, horse_number, horse_id, horse_name, sex, age, weight_carry, jockey_id, jockey_name, time_str, time_sec, margin_str, last_3f, odds, popularity, horse_weight, horse_weight_diff, trainer_id, trainer_name)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
''', new_results)

conn.commit()
conn.close()
print(f'Successfully inserted {len(new_races)} upcoming races for the weekend!')
