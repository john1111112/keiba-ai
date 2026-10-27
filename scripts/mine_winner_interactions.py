"""Discover combinations from past winners, freeze them, then audit on later races.

IMPORTANT:
* Research only. Never changes the shipped models, scheduler, alerts or any wagers.
* Winning horses are used to DISCOVER rules on <=2025 data ONLY.
* 2026 race outcomes are not inspected until the rule list has been frozen.
* Archived weather/going/body weight are not certified as genuinely pre-race.
* Results cannot be described as live performance or betting profit.
"""
import itertools
import json
import math
import sqlite3
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from src.features import build_features, FEATURE_COLS_V2_28
from src.asof_form_features import add_asof_form_features

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "interaction_rule_output"
OUT.mkdir(exist_ok=True)
QUERY = """SELECT r.race_id,r.race_name,r.date,r.race_course,r.race_number,
r.surface,r.distance,r.direction,r.weather,r.condition,r.grade,
res.finish_order,res.bracket_number,res.horse_number,res.horse_id,res.horse_name,
res.sex,res.age,res.weight_carry,res.jockey_id,res.jockey_name,res.time_str,
res.time_sec,res.margin_str,res.last_3f,res.odds,res.popularity,
res.horse_weight,res.horse_weight_diff,res.trainer_id,res.trainer_name,
res.sire,res.sire_type,res.running_style,res.corner_4_pos,res.rest_weeks
FROM races r JOIN race_results res ON r.race_id=res.race_id
WHERE r.date < '2026-10-26'
ORDER BY r.date,r.race_id,res.horse_number"""

with sqlite3.connect("file:" + str(ROOT / "data/keiba.db") + "?mode=ro", uri=True) as conn:
    raw = pd.read_sql_query(QUERY, conn)
raw["finish_order"] = pd.to_numeric(raw["finish_order"], errors="coerce")
valid = raw.finish_order.between(1, 28) & ~raw.time_str.fillna("").astype(str).str.strip().isin(["", "-"])
complete = set()
for rid, g in raw.groupby("race_id", sort=False):
    fin = g.loc[valid.loc[g.index]]
    ranks = fin.finish_order.astype(int).tolist()
    if len(g) >= 5 and len(fin) >= 5 and len(set(g.horse_number)) == len(g) and sorted(ranks) == list(range(1, len(ranks) + 1)):
        complete.add(str(rid))
assert len(complete) > 600

feat, cols = build_features(raw, feature_version="v2_28cols_no_pedigree", complete_race_ids=complete)
assert cols == list(FEATURE_COLS_V2_28)
feat, _ = add_asof_form_features(feat, complete)
# Include every enrolled horse in a complete historical race, including non-finishers.
# A race with exactly one known winner is necessary, and the current race's target is NEVER a feature.
f = feat.loc[feat.race_id.astype(str).isin(complete)].copy().reset_index(drop=True)
f["win"] = f.finish_order.eq(1).astype(int)
assert f.groupby("race_id").win.sum().eq(1).all()
f["race_id"] = f.race_id.astype(str)
train = f.loc[f.date < pd.Timestamp("2026-01-01")].copy().reset_index(drop=True)
test = f.loc[(f.date >= pd.Timestamp("2026-01-01")) & (f.date < pd.Timestamp("2026-10-26"))].copy().reset_index(drop=True)
assert train.race_id.nunique() >= 500 and test.race_id.nunique() >= 1000
assert train.date.max() < test.date.min()

# Separate **race-global** conditions from **individual horse** characteristics.
# Race-global context alone cannot identify a horse. Every rule includes at least one horse attribute.
def add_tokens(df):
    tokens = {}
    def add(family, name, mask):
        a = np.asarray(mask, dtype=bool)
        if 30 <= int(a[:TRAIN_ROWS].sum()) <= TRAIN_ROWS - 30:
            tokens[name] = (family, a)
    def category(col):
        return df[col].fillna("不明").astype(str).str.strip()
    course = category("race_course")
    for c in ["東京", "中山", "京都", "阪神", "中京", "新潟", "札幌", "函館", "福島", "小倉"]:
        add("course", "競馬場="+c, course.eq(c))
    surface = category("surface")
    for value in ["芝", "ダート"]:
        add("surface", "馬場種別="+value, surface.eq(value))
    weather = category("weather")
    for name, regex in [("雨", r"雨"), ("曇", r"曇"), ("晴", r"晴")]:
        add("weather", "天気="+name, weather.str.contains(regex, regex=True))
    condition = category("condition")
    for v in ["良", "稍重", "重", "不良"]:
        add("going", "馬場状態="+v, condition.eq(v))
    dist = pd.to_numeric(df.distance, errors="coerce")
    for name, lo, hi in [("短距離", 0, 1400), ("マイル", 1401, 1800), ("中距離", 1801, 2400), ("長距離", 2401, 9999)]:
        add("distance", "距離="+name, dist.between(lo, hi))
    direction = category("direction")
    for v in ["右", "左"]:
        add("direction", "回り="+v, direction.str.contains(v))

    # Individual horse attributes, known before post-time if the source has an archival timestamp.
    bracket = pd.to_numeric(df.bracket_number, errors="coerce")
    for name, lo, hi in [("内",1,3), ("中",4,6), ("外",7,8)]:
        add("bracket", "枠="+name, bracket.between(lo,hi))
    style = category("running_style")
    for v in ["逃げ","先行","差し","追込"]:
        add("style","過去脚質="+v,style.eq(v))
    prev_rank = pd.to_numeric(df.prev_rank, errors="coerce")
    prior_count = pd.to_numeric(df.past_race_count, errors="coerce")
    add("prev", "前走3着以内", (prior_count>=1) & (prev_rank<=3))
    add("prev", "前走4〜8着", (prior_count>=1) & prev_rank.between(4,8))
    add("prev", "前走9着以下", (prior_count>=1) & (prev_rank>=9))
    add("prior", "過去走不足", prior_count<3)
    add("prior", "過去3走以上",prior_count>=3)
    f3 = pd.to_numeric(df.horse_last3_finish_pct, errors="coerce")
    add("form", "近3走強い", (prior_count>=3)&(f3>=0.65))
    add("form", "近3走普通", (prior_count>=3)&f3.between(0.35,0.65,inclusive="left"))
    add("form", "近3走弱い", (prior_count>=3)&(f3<0.35))
    for c, label in [("jockey_win_rate","騎手通算勝率"),("trainer_win_rate_lag","調教師通算勝率")]:
        v = pd.to_numeric(df[c],errors="coerce")
        add(c,"高"+label,v>=0.12)
        add(c,"低"+label,v<=0.06)
    for c,name,low,high in [("horse_prev_dist_delta","距離変更大",400,None),
                             ("horse_ability_trend","近走上昇",0.15,None),
                             ("horse_ability_trend","近走下降",None,-0.15)]:
        v=pd.to_numeric(df[c],errors="coerce")
        add(c,name,(v>=low) if high is None else (v<=high))
    age=pd.to_numeric(df.age,errors="coerce")
    for name, mask in [("3歳以下",age<=3),("4歳",age.eq(4)),("5歳以上",age>=5)]:
        add("age","年齢="+name,mask)
    rest=pd.to_numeric(df.rest_weeks,errors="coerce")
    add("rest","中2〜5週",rest.between(2,5))
    add("rest","中6〜12週",rest.between(6,12))
    add("rest","13週以上",rest>=13)
    return tokens

# Keep exactly the same vocabulary across the two periods; no peeking at test outcome / prevalence.
whole = pd.concat([train, test], ignore_index=True)
TRAIN_ROWS=len(train)
token_all = add_tokens(whole)
ntrain=len(train)
race_context={"course","surface","weather","going","distance","direction"}
# Require one horse-specific token; combinations span 2 or 3 distinct families.
context_names=[name for name,(fam,_) in token_all.items() if fam in race_context]
horse_names=[name for name,(fam,_) in token_all.items() if fam not in race_context]
single_pairs = [(a,b) for a in context_names for b in horse_names]
context_pair_horse=[(a,b,c) for a,b in itertools.combinations(context_names,2)
                    if token_all[a][0]!=token_all[b][0] for c in horse_names]
context_horse_pair=[(a,b,c) for a in context_names
                    for b,c in itertools.combinations(horse_names,2) if token_all[b][0]!=token_all[c][0]]
candidates = single_pairs+context_pair_horse+context_horse_pair

def evaluate_mask(df, mask, include_score=None):
    """Within-race chance model, preserving the number of horses meeting the pattern per race."""
    rid, inverse=np.unique(df.race_id.to_numpy(),return_inverse=True)
    counts=np.bincount(inverse,minlength=len(rid))
    matching=np.bincount(inverse,weights=mask.astype(float),minlength=len(rid))
    p=matching/counts
    expectation=float(p.sum())
    variance=float(np.sum(p*(1-p)))
    actual=int((mask & df.win.eq(1).to_numpy()).sum())
    covered=int(mask.sum())
    hits_races=int(np.count_nonzero(matching))
    z=(actual-expectation)/math.sqrt(variance) if variance>0 else 0
    pvalue=1-NormalDist().cdf(z) if variance>0 else 1.0
    d={"runners":covered,"races":hits_races,"wins":actual,
       "random_expected_wins":round(expectation,4),"random_lift":round(actual/expectation,4) if expectation>0 else None,
       "z_vs_random":round(z,4),"p_one_sided_vs_random":round(pvalue,8)}
    if include_score is not None:
        # Compare with 28-feature baseline's predictive probability in *each race*.
        scores=np.asarray(include_score,dtype=float)
        norm=np.bincount(inverse,weights=scores,minlength=len(rid))
        calibrated=np.where(norm[inverse]>1e-12,scores/np.maximum(norm[inverse],1e-12),1/counts[inverse])
        expected=float(calibrated[mask].sum())
        d["baseline_model_expected_wins"]=round(expected,4)
        d["lift_vs_28_feature_model"]=round(actual/expected,4) if expected>0 else None
    return d

win_train=train.win.eq(1).to_numpy()
train_rid,train_inverse=np.unique(train.race_id.to_numpy(),return_inverse=True)
train_fields=np.bincount(train_inverse)
all_records=[]
max_checked=0
for rule in candidates:
    m=np.logical_and.reduce([token_all[t][1][:ntrain] for t in rule])
    matched=int(m.sum())
    if matched<65: continue
    runs=np.bincount(train_inverse,weights=m.astype(float),minlength=len(train_fields))
    races_covered=int((runs>0).sum())
    ex=float(np.sum(runs/train_fields))
    if races_covered<50 or ex<5: continue
    variance=float(np.sum((runs/train_fields)*(1-runs/train_fields)))
    if variance<=0: continue
    wins=int((m & win_train).sum())
    z=(wins-ex)/math.sqrt(variance)
    pval=1-NormalDist().cdf(z)
    all_records.append({"parts":list(rule),"pattern":" × ".join(rule),
                        "train_runners":matched,"train_races":races_covered,
                        "train_wins":wins,"train_random_expected":ex,
                        "train_lift":wins/ex,"train_z":z,"train_p":pval})
    max_checked+=1
assert max_checked>10, "Insufficient candidate pattern support"
candidates_df=pd.DataFrame(all_records).sort_values(["train_p","pattern"]).reset_index(drop=True)
n_tests=len(candidates_df)
# Benjamini-Hochberg FDR across every pattern attempted, not only good-looking ones.
raw_q = candidates_df.train_p.to_numpy() * n_tests / (np.arange(n_tests)+1)
candidates_df["train_q_BH"] = np.minimum.accumulate(raw_q[::-1])[::-1].clip(0,1)
# Choose fixed rules based on training data alone, before test outcomes are evaluated.
selected = candidates_df.loc[(candidates_df.train_z>0)&(candidates_df.train_q_BH<=0.1)].head(12)
if selected.empty:
    selected=candidates_df.loc[candidates_df.train_z>0].head(8)
    selection="EXPLORATORY_ONLY_NONE_FDR_SIGNIFICANT"
else:
    selection="TRAIN_ONLY_FDR_SIGNIFICANT"
chosen_rules=[list(parts) for parts in selected.parts]
# Now, and only now, compute test winners' relationship to the frozen candidate list.
model=LGBMClassifier(objective="binary",learning_rate=0.05,n_estimators=150,
                     num_leaves=31,max_depth=6,min_child_samples=20,
                     random_state=42,verbosity=-1,n_jobs=2)
model.fit(train[cols],train.win)
score=model.predict_proba(test[cols])[:,1]
test_win=test.win.eq(1).to_numpy()
tested=[]
for rule in chosen_rules:
    m=np.logical_and.reduce([token_all[t][1][ntrain:] for t in rule])
    evidence=evaluate_mask(test,m,include_score=score)
    row=next(x for x in all_records if x["parts"]==rule)
    tested.append({"pattern":" × ".join(rule),
                   "training_runners":row["train_runners"],
                   "training_wins":row["train_wins"],
                   "training_random_lift":round(row["train_lift"],3),
                   "training_adjusted_q":round(float(candidates_df.loc[candidates_df.pattern.eq(" × ".join(rule)),"train_q_BH"].iloc[0]),4),
                   **evidence})
report={
    "status":"HISTORICAL_PATTERN_MINING_NOT_LIVE_CERTIFIED",
    "training_last_date":str(train.date.max().date()),
    "evaluation_first_date":str(test.date.min().date()),
    "evaluation_last_date":str(test.date.max().date()),
    "training_races":int(train.race_id.nunique()),
    "evaluation_races":int(test.race_id.nunique()),
    "rules_screened_after_minimum_support":n_tests,
    "selection_method":selection,
    "selected_rule_count":len(tested),
    "pattern_results":tested,
    "important_limitations":[
        "Saved race-weather/going and runner data do not contain independently verified pre-race capture timestamps.",
        "No inner-versus-outer track geometry field is available; Kyoto outer-course claims are prohibited.",
        "2026 aggregate holdout has been viewed in previous model experiments, so it is not a completely pristine untouched benchmark.",
        "Training-rule p-values approximate the within-race random baseline; overlapping patterns may be correlated.",
        "Observed lift over random/expected winners does NOT imply positive betting expected value or superiority to closing favorites.",
        "This procedure is research-only; no Discord alert, JRA-VAN access or betting action was performed."
    ],
    "production_model_changed":False, "profit_claim":False
}
(OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
pd.DataFrame([{k:v for k,v in r.items() if k!="parts"} for r in all_records]).to_csv(OUT/"all_training_candidates.csv",index=False)
pd.DataFrame(tested).to_csv(OUT/"selected_patterns_holdout.csv",index=False)
print("INTERACTION_REPORT="+json.dumps(report,ensure_ascii=False))
