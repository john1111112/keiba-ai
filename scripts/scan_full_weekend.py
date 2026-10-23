import sys
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import sqlite3
import pandas as pd
from src.features import build_features
from src.model import KeibaAIModel
from src.betting_strategy import evaluate_race_value

conn = sqlite3.connect(str(project_root / 'data' / 'keiba.db'))
# 最新2000件の履歴 + 今週末の全レース
df_all = pd.read_sql_query('SELECT r.date, r.race_course, r.race_number, r.race_name, r.surface, r.distance, r.condition, res.* FROM race_results res JOIN races r ON res.race_id = r.race_id ORDER BY r.date DESC LIMIT 3000', conn)
conn.close()

df_feat, _ = build_features(df_all)
cache_p = project_root / 'data' / 'features.parquet'
df_feat.to_parquet(cache_p, index=False)

future = df_feat[df_feat['date'] >= '2026-10-24'].copy()
model = KeibaAIModel()
model.load()

ss_list = []
s_list = []
for rid, group in future.groupby('race_id'):
    pred = model.predict_race(group)
    val = evaluate_race_value(pred)
    rk = val.get('rank', '')
    r = group.iloc[0]
    date_str = r['date'].strftime('%Y-%m-%d')
    if 'SSランク' in rk:
        ss_list.append((date_str, r['race_course'], int(r['race_number']), r['race_name'], val['honmei']['horse_name']))
    elif 'Sランク' in rk:
        s_list.append((date_str, r['race_course'], int(r['race_number']), r['race_name'], val['honmei']['horse_name']))

print('Total weekend races scanned:', future['race_id'].nunique())
print('Found SS-Rank count:', len(ss_list))
for item in ss_list:
    print('  [SS]', item)
print('Found S-Rank count:', len(s_list))
for item in s_list:
    print('  [S]', item)
