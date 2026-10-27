"""No-odds retrospective 2025 win-picking evaluation. No bets and no 2026 labels."""
import json, math, sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from src.features import FEATURE_COLS_V2_28, build_features

ROOT=Path(__file__).resolve().parents[1]
BANNED={'finish_order','last_3f','time_str','time_sec','margin_str','corner_4_pos','odds','popularity','target','race_id','date','horse_name','horse_id','jockey_id'}
assert not BANNED.intersection(FEATURE_COLS_V2_28), 'Outcome/market leakage into X'
db=ROOT/'data/keiba.db'
assert db.is_file(), 'Historical DB not checked out'
query="""SELECT r.race_id,r.race_name,r.date,r.race_course,r.race_number,
r.surface,r.distance,r.direction,r.weather,r.condition,r.grade,
res.finish_order,res.bracket_number,res.horse_number,res.horse_id,res.horse_name,
res.sex,res.age,res.weight_carry,res.jockey_id,res.jockey_name,res.time_str,
res.time_sec,res.margin_str,res.last_3f,res.odds,res.popularity,
res.horse_weight,res.horse_weight_diff,res.trainer_id,res.trainer_name,
res.sire,res.sire_type,res.running_style,res.corner_4_pos,res.rest_weeks
FROM races r JOIN race_results res ON r.race_id=res.race_id
WHERE r.date < '2026-01-01'
ORDER BY r.date,r.race_id,res.horse_number"""
connection=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)
try:
    raw=pd.read_sql_query(query,connection)
finally:
    connection.close()
assert len(raw)>1000
raw['finish_order']=pd.to_numeric(raw.finish_order,errors='coerce')
raw['_valid']=raw.finish_order.between(1,28) & ~raw.time_str.fillna('').astype(str).str.strip().isin(['','-'])
good=[]
for race_id,g in raw.groupby('race_id',sort=False):
    finished=g.loc[g['_valid']]
    ranks=finished.finish_order.astype(int).tolist()
    if len(g)>=5 and len(finished)>=5 and len(set(g.horse_number))==len(g) and sorted(ranks)==list(range(1,len(ranks)+1)):
        good.append(str(race_id))
# build_features performs one-day batch updates, so today's results cannot enter today's features.
df,cols=build_features(raw,feature_version='v2_28cols_no_pedigree')
assert cols==FEATURE_COLS_V2_28
df=df[df.race_id.astype(str).isin(good)].copy()
df=df[df.finish_order.between(1,28) & ~df.time_str.fillna('').astype(str).str.strip().isin(['','-'])].copy()
assert df.race_id.nunique()>50
assert (df.groupby('race_id').finish_order.apply(lambda s:int(s.eq(1).sum()))==1).all()
# Choose holdout solely by chronological dates. No test outcomes can influence cutoff.
dates=sorted(df.date.astype(str).str[:10].unique())
cutoff=dates[int(len(dates)*0.7)]
train=df[df.date.astype(str).str[:10]<cutoff]
test=df[df.date.astype(str).str[:10]>=cutoff].copy()
assert train.race_id.nunique()>=20 and test.race_id.nunique()>=20
assert max(train.date)<min(test.date)
model=LGBMClassifier(objective='binary',learning_rate=0.05,n_estimators=150,
                    num_leaves=31,max_depth=6,min_child_samples=20,
                    random_state=42,verbosity=-1,n_jobs=2)
model.fit(train[cols],train.finish_order.eq(1).astype(int))
test['score']=model.predict_proba(test[cols])[:,1]
assert np.isfinite(test.score).all()
results=[]
for rid,g in test.groupby('race_id',sort=False):
    g=g.sort_values(['score','horse_number'],ascending=[False,True])
    p=g.score / g.score.sum()
    assert np.isfinite(p).all() and len(g)>=5
    winner=g[g.finish_order.eq(1)].iloc[0]
    winner_idx=g.index[g.finish_order.eq(1)][0]
    winner_rank=int(g.index.get_loc(winner_idx))+1
    results.append(dict(race_id=str(rid),date=str(winner.date)[:10],field_size=int(len(g)),
                        predicted_horse_number=int(g.horse_number.iloc[0]),
                        actual_winner_horse_number=int(winner.horse_number),
                        winner_rank=winner_rank,
                        winner_probability=float(p.loc[winner_idx]),
                        race_winner_nll=-math.log(max(float(p.loc[winner_idx]),1e-15)),
                        uniform_nll=math.log(len(g))))
r=pd.DataFrame(results)
report=dict(status='RETROSPECTIVE_CHRONOLOGICAL_VALIDATION_NOT_LIVE_CERTIFIED',
            training_last_date=str(max(train.date))[:10],
            validation_first_date=str(min(test.date))[:10],
            validation_last_date=str(max(test.date))[:10],
            training_races=int(train.race_id.nunique()),
            validation_races=int(len(r)),
            ai_top1_hits=int(r.winner_rank.eq(1).sum()),
            ai_top1_hit_rate=float(r.winner_rank.eq(1).mean()),
            ai_top2_hits=int(r.winner_rank.le(2).sum()),
            ai_top2_hit_rate=float(r.winner_rank.le(2).mean()),
            ai_top3_hits=int(r.winner_rank.le(3).sum()),
            ai_top3_hit_rate=float(r.winner_rank.le(3).mean()),
            uniform_top1_expected_rate=float((1/r.field_size).mean()),
            ai_race_winner_nll=float(r.race_winner_nll.mean()),
            uniform_race_winner_nll=float(r.uniform_nll.mean()),
            market_odds_used=False,postrace_popularity_used=False,
            holdout_outcomes_used_in_fit=False,
            verified_prerace_timestamp_of_archival_features=False,
            prediction_validity='historical retrospective only, not as-of certification',
            no_roi_estimated=True)
out=ROOT/'blind_audit_output'
out.mkdir(exist_ok=True)
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
r.to_csv(out/'predictions.csv',index=False,encoding='utf-8')
print('BLIND_AUDIT_REPORT='+json.dumps(report,ensure_ascii=False))
print('First ten:',r.head(10)[['date','race_id','predicted_horse_number','actual_winner_horse_number','winner_rank']].to_string(index=False))
