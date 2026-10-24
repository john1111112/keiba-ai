import pandas as pd
import numpy as np
from typing import Dict, Any
from src.betting_strategy import evaluate_race_value

def _filter_complete_past_races(df_evaluated: pd.DataFrame) -> pd.DataFrame:
    """
    1〜3着馬がすべて存在し、出走頭数6頭以上かつ完走着順が連続している完全な実在JRA確定レースのみを検証対象として抽出。
    また、is_warmup_block == True（初期学習専用期間）および pred_score が NaN の行を評価対象から除外する。
    """
    valid_df = df_evaluated[(df_evaluated["finish_order"] >= 1) & (df_evaluated["finish_order"] <= 28)].copy()
    if "is_warmup_block" in valid_df.columns:
        valid_df = valid_df[~valid_df["is_warmup_block"].fillna(False)].copy()
    if "pred_score" in valid_df.columns:
        valid_df = valid_df[valid_df["pred_score"].notna()].copy()
    if len(valid_df) == 0:
        return valid_df

    if "time_str" in valid_df.columns:
        t_series = valid_df["time_str"].fillna("").astype(str).str.strip()
        fin_df = valid_df[~t_series.isin(["", "-"])]
    else:
        fin_df = valid_df

    grp_stats = fin_df.groupby("race_id").agg(
        n_valid=("horse_number", "count"),
        max_fo=("finish_order", "max"),
        w1=("finish_order", lambda s: (s == 1).any()),
        w2=("finish_order", lambda s: (s == 2).any()),
        w3=("finish_order", lambda s: (s == 3).any()),
    )
    entry_counts = valid_df.groupby("race_id")["horse_number"].count()
    grp_stats["entry_count"] = entry_counts
    complete_rids = grp_stats[
        (grp_stats["entry_count"] >= 6)
        & grp_stats["w1"]
        & grp_stats["w2"]
        & grp_stats["w3"]
        & (grp_stats["max_fo"] <= grp_stats["n_valid"])
    ].index
    return valid_df[valid_df["race_id"].isin(complete_rids)].copy()

def run_backtest(df_evaluated: pd.DataFrame) -> Dict[str, Any]:
    """
    【推定式ゼロ・JRA公式確定単勝オッズ完全準拠】
    ウォークフォワード検証による「全レース1番人気ベタ買い」vs「AI推奨レース単勝買い」の実収支比較。
    すべての払戻金はJRA公式確定単勝オッズ(odds * 賭け金)のみで厳密に計算し、架空の複勝/馬連推定式は一切使わない。
    """
    valid_df = _filter_complete_past_races(df_evaluated)
    valid_df = valid_df.sort_values(["date", "race_id"]).copy()
    grouped = valid_df.groupby("race_id", sort=False)
    total_races = len(grouped)

    raw_bets = 0
    raw_returns = 0
    raw_hits = 0

    smart_bets = 0
    smart_returns = 0
    smart_hits = 0
    smart_races = 0

    ana_bets = 0
    ana_returns = 0
    ana_hits = 0
    ana_races = 0

    timeline = []
    cum_raw = 0
    cum_smart = 0
    cum_ana = 0

    for race_id, group in grouped:
        sorted_g = group.sort_values("pred_score", ascending=False).reset_index(drop=True)
        if len(sorted_g) < 6:
            continue

        top1 = sorted_g.iloc[0]
        odds = float(top1["odds"])
        fo1 = int(top1["finish_order"])

        # 1. 全レース1番人気ベタ買い (100円)
        pop_sorted = group.sort_values("popularity", ascending=True).reset_index(drop=True)
        fav1 = pop_sorted.iloc[0]
        raw_bets += 100
        if int(fav1["finish_order"]) == 1:
            raw_hits += 1
            ret = int(round(float(fav1["odds"]) * 100))
            raw_returns += ret
            cum_raw += (ret - 100)
        else:
            cum_raw -= 100

        # 2. AI勝負ランク(SS/S/A)単勝買い (1,000円・JRA公式単勝配当のみ)
        val_res = evaluate_race_value(sorted_g)
        rank = val_res["rank"]
        if any(k in rank for k in ("SSランク", "Sランク", "Aランク")) and odds > 0:
            smart_races += 1
            smart_bets += 1000
            if fo1 == 1:
                smart_hits += 1
                s_ret = int(round(odds * 1000))
                smart_returns += s_ret
                cum_smart += (s_ret - 1000)
            else:
                cum_smart -= 1000

        # 3. AI穴馬(単勝10〜30倍かつAI予測確率25%以上)単勝買い (500円・JRA公式単勝配当のみ)
        ana_horses = sorted_g[(sorted_g["pred_score"] >= 0.25) & (sorted_g["odds"] >= 10.0) & (sorted_g["odds"] <= 30.0)]
        if len(ana_horses) > 0:
            target_ana = ana_horses.iloc[0]
            a_odds = float(target_ana["odds"])
            a_fo = int(target_ana["finish_order"])
            ana_races += 1
            ana_bets += 500
            if a_fo == 1:
                ana_hits += 1
                a_ret = int(round(a_odds * 500))
                ana_returns += a_ret
                cum_ana += (a_ret - 500)
            else:
                cum_ana -= 500

        timeline.append({
            "race_id": race_id,
            "cum_raw": cum_raw,
            "cum_smart": cum_smart,
            "cum_ana": cum_ana
        })

    raw_roi = round(raw_returns / raw_bets * 100, 1) if raw_bets > 0 else 0.0
    smart_roi = round(smart_returns / smart_bets * 100, 1) if smart_bets > 0 else 0.0
    ana_roi = round(ana_returns / ana_bets * 100, 1) if ana_bets > 0 else 0.0
    smart_hit_rate = round(smart_hits / smart_races * 100, 1) if smart_races > 0 else 0.0
    raw_hit_rate = round(raw_hits / total_races * 100, 1) if total_races > 0 else 0.0
    ana_hit_rate = round(ana_hits / ana_races * 100, 1) if ana_races > 0 else 0.0

    return {
        "total_races": total_races,
        "raw_tansho": {
            "races": total_races,
            "bets": raw_bets,
            "returns": raw_returns,
            "profit": raw_returns - raw_bets,
            "recovery_rate": raw_roi,
            "hit_rate": raw_hit_rate,
            "hits": raw_hits
        },
        "smart_select": {
            "races": smart_races,
            "skip_races": total_races - smart_races,
            "bets": smart_bets,
            "returns": smart_returns,
            "profit": smart_returns - smart_bets,
            "recovery_rate": smart_roi,
            "hit_rate": smart_hit_rate,
            "hits": smart_hits
        },
        "ana_select": {
            "races": ana_races,
            "bets": ana_bets,
            "returns": ana_returns,
            "profit": ana_returns - ana_bets,
            "recovery_rate": ana_roi,
            "hit_rate": ana_hit_rate,
            "hits": ana_hits
        },
        "timeline": pd.DataFrame(timeline)
    }


def analyze_rank_performance(df_evaluated: pd.DataFrame) -> Dict[str, Any]:
    """
    【推定式ゼロ・JRA公式単勝配当ベース】
    判定ランク別（SS / S / A / SS+S合計 / 全レース）のウォークフォワード検証成績。
    払戻金はすべてJRA公式確定単勝オッズ(odds * 賭け金)で厳密に計算し、1着勝率(hit_rate)と3着以内複勝率(place_rate)を併記する。
    """
    valid_df = _filter_complete_past_races(df_evaluated)
    valid_df = valid_df.sort_values(["date", "race_id"]).copy()
    grouped = valid_df.groupby("race_id", sort=False)
    total_races = len(grouped)

    stats = {
        "🔥 SSランクのみ (S基準×15倍以上)": {"races": 0, "bets": 0, "returns": 0, "hits": 0, "place_hits": 0, "cum": []},
        "⚡ Sランクのみ (超高勝率・本命)": {"races": 0, "bets": 0, "returns": 0, "hits": 0, "place_hits": 0, "cum": []},
        "⭐ Aランクのみ (自信あり)": {"races": 0, "bets": 0, "returns": 0, "hits": 0, "place_hits": 0, "cum": []},
        "SS+Sランク合計 (激アツ勝負)": {"races": 0, "bets": 0, "returns": 0, "hits": 0, "place_hits": 0, "cum": []},
        "全レース (見送りなし)": {"races": 0, "bets": 0, "returns": 0, "hits": 0, "place_hits": 0, "cum": []}
    }

    cum_ss = 0
    cum_s = 0
    cum_a = 0
    cum_ss_s = 0
    cum_all = 0

    for race_id, group in grouped:
        sorted_g = group.sort_values("pred_score", ascending=False).reset_index(drop=True)
        if len(sorted_g) < 6:
            continue

        val_res = evaluate_race_value(sorted_g)
        rank = val_res["rank"]
        top1 = sorted_g.iloc[0]
        odds = float(top1["odds"])
        fo1 = int(top1["finish_order"])

        is_win = (fo1 == 1)
        is_place = (1 <= fo1 <= 3)

        # 1. 全レース (AI評価1位の単勝1,000円ベタ買い)
        bet_all = 1000
        stats["全レース (見送りなし)"]["races"] += 1
        stats["全レース (見送りなし)"]["bets"] += bet_all
        ret_all = int(round(odds * bet_all)) if is_win else 0
        stats["全レース (見送りなし)"]["returns"] += ret_all
        if is_win:
            stats["全レース (見送りなし)"]["hits"] += 1
        if is_place:
            stats["全レース (見送りなし)"]["place_hits"] += 1
        cum_all += (ret_all - bet_all)
        stats["全レース (見送りなし)"]["cum"].append(cum_all)

        # 2. SSランク (JRA公式単勝配当で厳密計算)
        if "SSランク" in rank:
            bet_ss = 1000
            ret_ss = int(round(odds * bet_ss)) if is_win else 0
            stats["🔥 SSランクのみ (S基準×15倍以上)"]["races"] += 1
            stats["🔥 SSランクのみ (S基準×15倍以上)"]["bets"] += bet_ss
            stats["🔥 SSランクのみ (S基準×15倍以上)"]["returns"] += ret_ss
            if is_win:
                stats["🔥 SSランクのみ (S基準×15倍以上)"]["hits"] += 1
            if is_place:
                stats["🔥 SSランクのみ (S基準×15倍以上)"]["place_hits"] += 1
            cum_ss += (ret_ss - bet_ss)

            stats["SS+Sランク合計 (激アツ勝負)"]["races"] += 1
            stats["SS+Sランク合計 (激アツ勝負)"]["bets"] += bet_ss
            stats["SS+Sランク合計 (激アツ勝負)"]["returns"] += ret_ss
            if is_win:
                stats["SS+Sランク合計 (激アツ勝負)"]["hits"] += 1
            if is_place:
                stats["SS+Sランク合計 (激アツ勝負)"]["place_hits"] += 1
            cum_ss_s += (ret_ss - bet_ss)

        # 3. Sランク (JRA公式単勝配当で厳密計算)
        elif "Sランク" in rank:
            bet_s = 1000
            ret_s = int(round(odds * bet_s)) if is_win else 0
            stats["⚡ Sランクのみ (超高勝率・本命)"]["races"] += 1
            stats["⚡ Sランクのみ (超高勝率・本命)"]["bets"] += bet_s
            stats["⚡ Sランクのみ (超高勝率・本命)"]["returns"] += ret_s
            if is_win:
                stats["⚡ Sランクのみ (超高勝率・本命)"]["hits"] += 1
            if is_place:
                stats["⚡ Sランクのみ (超高勝率・本命)"]["place_hits"] += 1
            cum_s += (ret_s - bet_s)

            stats["SS+Sランク合計 (激アツ勝負)"]["races"] += 1
            stats["SS+Sランク合計 (激アツ勝負)"]["bets"] += bet_s
            stats["SS+Sランク合計 (激アツ勝負)"]["returns"] += ret_s
            if is_win:
                stats["SS+Sランク合計 (激アツ勝負)"]["hits"] += 1
            if is_place:
                stats["SS+Sランク合計 (激アツ勝負)"]["place_hits"] += 1
            cum_ss_s += (ret_s - bet_s)

        stats["🔥 SSランクのみ (S基準×15倍以上)"]["cum"].append(cum_ss)
        stats["⚡ Sランクのみ (超高勝率・本命)"]["cum"].append(cum_s)
        stats["SS+Sランク合計 (激アツ勝負)"]["cum"].append(cum_ss_s)

        # 4. Aランク (JRA公式単勝配当で厳密計算)
        if "Aランク" in rank:
            bet_a = 1000
            ret_a = int(round(odds * bet_a)) if is_win else 0
            stats["⭐ Aランクのみ (自信あり)"]["races"] += 1
            stats["⭐ Aランクのみ (自信あり)"]["bets"] += bet_a
            stats["⭐ Aランクのみ (自信あり)"]["returns"] += ret_a
            if is_win:
                stats["⭐ Aランクのみ (自信あり)"]["hits"] += 1
            if is_place:
                stats["⭐ Aランクのみ (自信あり)"]["place_hits"] += 1
            cum_a += (ret_a - bet_a)

        stats["⭐ Aランクのみ (自信あり)"]["cum"].append(cum_a)

    results = {}
    for k, v in stats.items():
        n = v["races"]
        b = v["bets"]
        r = v["returns"]
        h = v["hits"]
        ph = v.get("place_hits", 0)
        results[k] = {
            "races": n,
            "skip_races": total_races - n,
            "bets": b,
            "returns": r,
            "profit": r - b,
            "recovery_rate": round(r / b * 100, 1) if b > 0 else 0.0,
            "hit_rate": round(h / n * 100, 1) if n > 0 else 0.0,
            "hits": h,
            "place_rate": round(ph / n * 100, 1) if n > 0 else 0.0,
            "place_hits": ph,
            "cum": v["cum"]
        }
    return results
