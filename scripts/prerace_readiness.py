"""Offline race-level readiness report: never label manual odds as certified."""
import argparse
import json
import sqlite3
from pathlib import Path

from scripts.detect_odds_drops import detect_drops, parse_time


def race_readiness(db_path, race_id, *, as_of_time, race_start_time):
    asof, start = parse_time(as_of_time), parse_time(race_start_time)
    if asof is None or start is None or asof >= start:
        raise ValueError("Need timezone-aware as_of_time < race_start_time")
    db = Path(db_path)
    if not db.is_file():
        return {"race_id": str(race_id), "status": "NO_BET", "reason": "NO_ODDS_DB"}
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as conn:
        odds = conn.execute("""
            SELECT horse_number, odds_value, observed_at, fetched_at, source, data_status
            FROM odds_timeline WHERE race_id = ? AND odds_type = 'win'
        """, (str(race_id),)).fetchall()
        try:
            predictions = conn.execute("""
                SELECT horse_number, pred_win_prob, predicted_at, odds_status
                FROM ai_predictions_timeline WHERE race_id = ?
            """, (str(race_id),)).fetchall()
        except sqlite3.OperationalError:
            predictions = []

    latest = {}
    for horse, price, observed, fetched, source, status in odds:
        obs, got = parse_time(observed), parse_time(fetched)
        if (not obs or not got or obs > got or got > asof or got >= start
                or obs >= start or str(status).lower() in
                {"final", "final_settled", "post_race", "settled", "result"}):
            continue
        if horse not in latest or obs > latest[horse][0]:
            latest[horse] = (obs, price, source, status)
    valid_preds = []
    for horse, prob, predicted_at, status in predictions:
        at = parse_time(predicted_at)
        if at and at <= asof and at < start and status == "captured" and prob is not None:
            valid_preds.append((horse, prob))
    movement = detect_drops(db, race_id=race_id, as_of_time=as_of_time,
                            race_start_time=race_start_time)
    return {
        "race_id": str(race_id),
        "as_of_time": as_of_time,
        "race_start_time": race_start_time,
        "status": "NO_BET",
        "reason": "UNVERIFIED_ODDS_PROVENANCE_OR_INCOMPLETE_PREDICTIONS",
        "eligible_odds_horses": len(latest),
        "eligible_prediction_rows": len(valid_preds),
        "manual_or_uncertified_odds": sum(
            1 for _, _, source, status in latest.values()
            if str(status).lower().startswith("manual") or str(source).lower().startswith("manual")),
        "odds_drop_alerts": movement.get("alerts", []),
        "note": "Diagnostic only. No wagering or EV certification; no inferred odds."
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only pre-race readiness report")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--as-of-time", required=True)
    parser.add_argument("--race-start-time", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    report = race_readiness(args.db, args.race_id,
                            as_of_time=args.as_of_time,
                            race_start_time=args.race_start_time)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
