"""Research-only as-of-date features. Does not modify saved production models."""
from collections import defaultdict
from typing import Set
import math
import pandas as pd
import numpy as np

ASOF_FORM_COLS = [
    "trainer_win_rate_lag", "trainer_place_rate_lag", "trainer_prior_runs",
    "jockey_recent_win_rate", "jockey_recent_place_rate", "jockey_recent_runs",
    "horse_last3_finish_pct", "horse_last5_finish_pct",
    "horse_last3_top3_rate", "horse_last5_win_rate", "horse_ability_trend",
    "horse_prev_dist_delta", "horse_surface_switch",
    "horse_last3_final3f_relative", "horse_last5_same_surface_top3",
    "horse_last5_same_course_top3", "horse_prev3_rest_days",
    "current_field_size", "weight_vs_last_run",
]

def _key(value):
    if value is None or str(value).strip().lower() in ("", "nan", "none", "0", "不明"):
        return None
    return str(value)

def _float(value, default=np.nan):
    try:
        n = float(value)
        return n if math.isfinite(n) else default
    except (TypeError, ValueError):
        return default

def add_asof_form_features(df: pd.DataFrame, complete_race_ids: Set[str]):
    """
    Inference for every race on a calendar date uses only strictly earlier dates.
    Update horse/jockey/trainer histories AFTER features for the whole day.
    Update only fully populated historical races, never partial scraped records.
    The output is research-only and has no wagering or notification side effects.
    """
    frame = df.copy().reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["date", "race_id", "horse_number"]).reset_index(drop=True)
    rows = frame.to_dict("records")
    outputs = {c: np.zeros(len(frame), dtype=np.float64) for c in ASOF_FORM_COLS}
    horse_history = defaultdict(list)
    trainer_record = defaultdict(lambda: [0, 0, 0])
    jockey_last30 = defaultdict(list)
    field_size = frame.groupby("race_id").size().to_dict()

    for date, idxs in frame.groupby("date", sort=True).indices.items():
        # All runner features from previous dates only.
        for i in idxs:
            r = rows[int(i)]
            h_id, t_id, j_id = map(_key, (r.get("horse_id"), r.get("trainer_id"), r.get("jockey_id")))
            past = horse_history.get(h_id, []) if h_id else []
            t_runs, t_wins, t_places = trainer_record.get(t_id, (0, 0, 0)) if t_id else (0, 0, 0)
            outputs["trainer_win_rate_lag"][i] = (t_wins+2.0)/(t_runs+25.0)
            outputs["trainer_place_rate_lag"][i] = (t_places+5.5)/(t_runs+25.0)
            outputs["trainer_prior_runs"][i] = t_runs
            js = jockey_last30.get(j_id, []) if j_id else []
            outputs["jockey_recent_win_rate"][i] = (sum(v[0] for v in js)+1.6)/(len(js)+20)
            outputs["jockey_recent_place_rate"][i] = (sum(v[1] for v in js)+4.4)/(len(js)+20)
            outputs["jockey_recent_runs"][i] = len(js)
            outputs["current_field_size"][i] = field_size.get(r["race_id"], 0)
            outputs["horse_last3_finish_pct"][i] = 0.5
            outputs["horse_last5_finish_pct"][i] = 0.5
            outputs["horse_last3_top3_rate"][i] = 0.2
            outputs["horse_last5_win_rate"][i] = 0.07
            outputs["horse_ability_trend"][i] = 0
            outputs["horse_prev_dist_delta"][i] = 0
            outputs["horse_surface_switch"][i] = 0
            outputs["horse_last3_final3f_relative"][i] = 0
            outputs["horse_last5_same_surface_top3"][i] = 0.2
            outputs["horse_last5_same_course_top3"][i] = 0.2
            outputs["horse_prev3_rest_days"][i] = 56
            outputs["weight_vs_last_run"][i] = 0

            if past:
                last3, last5 = past[-3:], past[-5:]
                outputs["horse_last3_finish_pct"][i] = float(np.mean([x["normalized"] for x in last3]))
                outputs["horse_last5_finish_pct"][i] = float(np.mean([x["normalized"] for x in last5]))
                outputs["horse_last3_top3_rate"][i] = float(np.mean([x["place"] for x in last3]))
                outputs["horse_last5_win_rate"][i] = float(np.mean([x["win"] for x in last5]))
                if len(past)>=4:
                    early=past[-6:-3]
                    outputs["horse_ability_trend"][i] = (float(np.mean([x["normalized"] for x in last3]))-
                                                          float(np.mean([x["normalized"] for x in early])))
                outputs["horse_prev_dist_delta"][i] = abs(_float(r.get("distance"),0) - past[-1]["distance"])
                outputs["horse_surface_switch"][i] = int(str(r.get("surface")) != past[-1]["surface"])
                outputs["horse_last3_final3f_relative"][i] = float(np.mean([x["relative_last3f"] for x in last3]))
                surf = [x["place"] for x in last5 if x["surface"] == str(r.get("surface"))]
                course = [x["place"] for x in last5 if x["course"] == str(r.get("race_course"))]
                outputs["horse_last5_same_surface_top3"][i] = float(np.mean(surf)) if surf else 0.2
                outputs["horse_last5_same_course_top3"][i] = float(np.mean(course)) if course else 0.2
                outputs["horse_prev3_rest_days"][i] = min(365, max(0,(pd.Timestamp(date)-past[-1]["date"]).days))
                current_weight = _float(r.get("horse_weight"))
                prev_weight = past[-1]["horse_weight"]
                if np.isfinite(current_weight) and np.isfinite(prev_weight):
                    outputs["weight_vs_last_run"][i] = current_weight - prev_weight

        # Start of day boundary: now and only now may actual results update histories.
        day_rows=[rows[int(i)] for i in idxs if str(rows[int(i)]["race_id"]) in complete_race_ids]
        race_med={}
        for r in day_rows:
            if r["race_id"] not in race_med:
                values = [_float(q.get("last_3f")) for q in day_rows
                          if q["race_id"]==r["race_id"] and 1 <= _float(q.get("finish_order"),0) <= 28]
                values = [v for v in values if np.isfinite(v) and 28.0<=v<=65.0]
                race_med[r["race_id"]]=float(np.median(values)) if values else 35.5
        for r in day_rows:
            fo = _float(r.get("finish_order"),0)
            if not (1 <= fo <= 28) or str(r.get("time_str","")).strip() in ("","-","None","nan"):
                continue
            h_id, t_id, j_id = map(_key,(r.get("horse_id"),r.get("trainer_id"),r.get("jockey_id")))
            win, place=int(fo==1),int(fo<=3)
            if t_id:
                st=trainer_record[t_id];st[0]+=1;st[1]+=win;st[2]+=place
            if j_id:
                js=jockey_last30[j_id]
                js.append((win,place))
                if len(js)>30: del js[0]
            if h_id:
                f=field_size.get(r["race_id"],0)
                if f<2: continue
                observed3f=_float(r.get("last_3f"))
                rel3f = (race_med[r["race_id"]] - observed3f) if np.isfinite(observed3f) and 28.0<=observed3f<=65.0 else 0.0
                horse_history[h_id].append({
                    "normalized": float(np.clip(1-(fo-1)/(f-1),0,1)),
                    "place": place, "win":win,
                    "distance": _float(r.get("distance"),0),
                    "surface":str(r.get("surface")),
                    "course":str(r.get("race_course")),
                    "relative_last3f":rel3f, "date":pd.Timestamp(date),
                    "horse_weight":_float(r.get("horse_weight")),
                })
                if len(horse_history[h_id])>20: del horse_history[h_id][0]
    for name in ASOF_FORM_COLS:
        frame[name] = outputs[name]
        assert np.isfinite(frame[name].to_numpy(dtype=float)).all(), name
    return frame, list(ASOF_FORM_COLS)
