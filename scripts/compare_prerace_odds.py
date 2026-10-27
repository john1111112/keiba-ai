"""Compare two strictly pre-race odds cutoffs without inventing missing prices."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from math import isfinite

from scripts.detect_odds_drops import parse_time
from scripts.race_odds_timeline import race_odds_timeline


def compare_odds(db_path, race_id, *, earlier, later, race_start_time):
    t0, t1, start = (parse_time(v) for v in (earlier, later, race_start_time))
    if any(t is None for t in (t0, t1, start)) or not t0 < t1 < start:
        raise ValueError("Require timezone-aware earlier < later < race start")
    history = race_odds_timeline(db_path, race_id, as_of_time=t1.isoformat(),
                                 race_start_time=start.isoformat())
    earlier_by_horse = {}
    later_by_horse = {}
    for snapshot in history["observations"]:
        published = parse_time(snapshot["observed_at"])
        for horse in snapshot["horses"]:
            fetched = parse_time(horse["fetched_at"])
            number = horse["horse_number"]
            value = horse["win_odds"]
            if fetched is None or not isfinite(value) or value < 1:
                continue
            if published <= t0 and fetched <= t0:
                earlier_by_horse[number] = (published, fetched, value)
            if published <= t1 and fetched <= t1:
                later_by_horse[number] = (published, fetched, value)
    changes = []
    for number in sorted(earlier_by_horse.keys() & later_by_horse.keys()):
        before, after = earlier_by_horse[number], later_by_horse[number]
        # Only compare when the second observation is newer.
        if after[:2] <= before[:2]:
            continue
        change = round((after[2] / before[2] - 1) * 100, 3)
        changes.append({
            "horse_number": number,
            "earlier_odds": before[2],
            "later_odds": after[2],
            "change_percent": change,
            "direction": "SHORTENED" if change < 0 else "DRIFTED" if change > 0 else "UNCHANGED",
            "earlier_observed_at": before[0].isoformat(),
            "later_observed_at": after[0].isoformat(),
            "source_certified": False,
        })
    return {
        "race_id": str(race_id), "status": history["status"],
        "earlier": t0.isoformat(), "later": t1.isoformat(),
        "compared_horses": len(changes),
        "missing_earlier": sorted(later_by_horse.keys() - earlier_by_horse.keys()),
        "changes": changes, "bet_status": "NO_BET",
        "model_inference_allowed": False,
        "provenance_certified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline pre-race odds change report")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--earlier", required=True)
    parser.add_argument("--later", required=True)
    parser.add_argument("--race-start-time", required=True)
    args = parser.parse_args(argv)
    result = compare_odds(args.db, args.race_id, earlier=args.earlier,
                          later=args.later, race_start_time=args.race_start_time)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
