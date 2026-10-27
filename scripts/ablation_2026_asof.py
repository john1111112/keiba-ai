"""2026 chronological ablation: baseline 28 vs historical-only form+trainer features.
Read-only historical evaluation; NEVER retrains Windows production model or sends alerts.
2026 archive is not certified timestamped pre-race data, therefore no production acceptance.
"""
import json, sqlite3, math
from pathlib import Path
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from src.features import build_features, FEATURE_COLS_V2_28
from src.asof_form_features import add_asof_form_features, ASOF_FORM_COLS

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"feature_ablation_output"
OUT.mkdir(exist_ok=True)
BASE=list(FEATURE_COLS_V2_28)
EXT=BASE+list(ASOF_FORM_COLS)
BANNED={"finish_order","target","odds","popularity","last_3f","time_sec",
"margin_str","time_str","corner_4_pos","race_id","date","horse_id","jockey_id",
"trainer_id","horse_name"}
assert not BANNED.intersection(EXT) and len(set(EXT))==len(EXT)
query="""SELECT r.race_id,r.race_name,r.date,r.race_course,r.race_number,
r.surface,r.distance,r.direction,r.weather,r.condition,r.grade,
res.finish_order,res.bracket_number,res.horse_number,res.horse_id,res.horse_name,
res.sex,res.age,res.weight_carry,res.jockey_id,res.jockey_name,res.time_str,
res.time_sec,res.margin_str,res.last_3f,res.odds,res.popularity,
res.horse_weight,res.horse_weight_diff,res.trainer_id,res.trainer_name,
res.sire,res.sire_type,res.running_style,res.corner_4_pos,res.rest_weeks
FROM races r JOIN race_results res ON r.race_id=res.race_id
WHERE r.date < '2026-10-26'
ORDER BY r.date,r.race_id,res.horse_number"""
with sqlite3.connect("file:"+str(ROOT/"data/keiba.db")+"?mode=ro",uri=True) as db:
    raw=pd.read_sql_query(query,db)
raw["finish_order"]=pd.to_numeric(raw.finish_order,errors="coerce")
valid=raw.finish_order.between(1,28)&~raw.time_str.fillna("").astype(str).str.strip().isin(["","-"])
complete=set()
for rid,g in raw.groupby("race_id",sort=False):
    fin=g.loc[valid.loc[g.index]]
    ranks=fin.finish_order.astype(int).tolist()
    if len(g)>=5 and len(fin)>=5 and len(set(g.horse_number))==len(g) and sorted(ranks)==list(range(1,len(ranks)+1)):
        complete.add(str(rid))
assert len(complete)>600
feat,baseline_cols=build_features(raw,feature_version="v2_28cols_no_pedigree",complete_race_ids=complete)
assert baseline_cols==BASE
allfeat,added=add_asof_form_features(feat,complete)
assert added==ASOF_FORM_COLS
rows=allfeat[allfeat.race_id.astype(str).isin(complete)&
    allfeat.finish_order.between(1,28)&
    ~allfeat.time_str.fillna("").astype(str).str.strip().isin(["","-"])].copy()
train=rows[rows.date<pd.Timestamp("2026-01-01")].copy()
test=rows[(rows.date>=pd.Timestamp("2026-01-01"))&(rows.date<pd.Timestamp("2026-10-26"))].copy()
assert train.race_id.nunique()>500 and test.race_id.nunique()>500
assert train.date.max()<test.date.min()
assert (test.groupby("race_id").finish_order.apply(lambda x:int(x.eq(1).sum()))==1).all()

outcomes=[]
for label,cols in [("baseline_28",BASE),("extended_asof",EXT)]:
    model=LGBMClassifier(objective="binary",learning_rate=0.05,n_estimators=150,
        num_leaves=31,max_depth=6,min_child_samples=20,random_state=42,verbosity=-1,n_jobs=2)
    model.fit(train[cols],train.finish_order.eq(1).astype(int))
    pred=test[["race_id","date","horse_number","finish_order"]].copy()
    pred["score"]=model.predict_proba(test[cols])[:,1]
    assert np.isfinite(pred.score.to_numpy()).all()
    for rid,g in pred.groupby("race_id",sort=False):
        ranked=g.sort_values(["score","horse_number"],ascending=[False,True])
        winner=ranked.loc[ranked.finish_order.eq(1)].iloc[0]
        winner_rank=int(np.flatnonzero(ranked.horse_number.to_numpy()==winner.horse_number)[0])+1
        p=float(winner.score)/float(ranked.score.sum())
        outcomes.append(dict(model=label,race_id=str(rid),date=str(winner.date)[:10],
           field_size=len(ranked),winner_rank=winner_rank,
           top1_horse_number=int(ranked.horse_number.iloc[0]),
           winner_horse_number=int(winner.horse_number),
           winner_log_loss=-math.log(max(p,1e-15))))
output=pd.DataFrame(outcomes)
summary={}
for model,g in output.groupby("model"):
    summary[model]=dict(n_races=int(len(g)),top1_hits=int(g.winner_rank.eq(1).sum()),
        top1_rate=float(g.winner_rank.eq(1).mean()),
        top3_hits=int(g.winner_rank.le(3).sum()),
        top3_rate=float(g.winner_rank.le(3).mean()),
        mean_winner_log_loss=float(g.winner_log_loss.mean()))
paired=output.pivot(index="race_id",columns="model",values="winner_rank")
assert paired.notna().all().all()
assert len(paired)==len(output)/2
base=summary["baseline_28"];extended=summary["extended_asof"]
report=dict(status="RETROSPECTIVE_NOT_TIMESTAMP_CERTIFIED",train_last_date=str(train.date.max())[:10],
   test_first_date=str(test.date.min())[:10],test_last_date=str(test.date.max())[:10],
   train_races=int(train.race_id.nunique()),test_races=int(test.race_id.nunique()),
   baseline=base,extended=extended,
   top1_rate_delta_pct_points=100*(extended["top1_rate"]-base["top1_rate"]),
   top3_rate_delta_pct_points=100*(extended["top3_rate"]-base["top3_rate"]),
   winner_nll_change=extended["mean_winner_log_loss"]-base["mean_winner_log_loss"],
   no_final_odds_as_input=True,no_roi_evaluated=True,
   no_test_outcomes_in_model_fit=True,
   historical_features_with_current_race_outcomes_excluded=True,
   archived_pre_race_availability_not_certified=True,
   candidate_should_replace_production=False)
(OUT/"report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
output.to_csv(OUT/"predictions.csv",index=False)
print("ASOF_ABLATION_REPORT="+json.dumps(report,ensure_ascii=False))
