"""Read-only offline data-quality audit for recorded pre-race odds.

This audit never certifies a provider, computes betting EV, or modifies data.
It exposes rejected rows and per-race snapshot coverage so local ingestion
problems are visible before any research/backtest.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from scripts.detect_odds_drops import parse_time


ALLOWED = {"pre_race_flash", "manual_prerace"}


def audit_odds_history(db_path, *, as_of_time=None, race_starts=None):
    race_starts = race_starts or {}
    if not isinstance(race_starts, dict):
        raise ValueError("race_starts must be a mapping")
    cutoff = parse_time(as_of_time) if as_of_time is not None else None
    if as_of_time is not None and cutoff is None:
        raise ValueError("as_of_time must include timezone")
    starts = {str(k): parse_time(v) for k, v in race_starts.items()}
    if any(value is None for value in starts.values()):
        raise ValueError("Race start timestamps must include timezone")
    db = Path(db_path)
    result = {"status": "NO_ODDS_DB", "bet_status": "NO_BET",
              "asof_certified": False, "total_rows": 0, "eligible_rows": 0,
              "rejected": {}, "races": []}
    if not db.is_file():
        return result
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as conn:
        try:
            rows = conn.execute("""
                SELECT race_id, horse_number, odds_type, odds_value,
                       observed_at, fetched_at, source, data_status
                FROM odds_timeline ORDER BY race_id, id
            """).fetchall()
        except sqlite3.OperationalError:
            result["status"] = "NO_ODDS_TABLE"
            return result
    reasons = Counter()
    race_stats = defaultdict(lambda: {"eligible_rows": 0, "horses": set(),
                                      "observations": set(), "manual_rows": 0})
    for race, horse, kind, odds, observed, fetched, source, status in rows:
        rid = str(race)
        reason = None
        obs, got = parse_time(observed), parse_time(fetched)
        try:
            number = int(horse)
            price = float(odds)
            if isinstance(horse, bool) or str(horse).strip() != str(number):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            number, price = 0, float("nan")
        if kind != "win":
            reason = "NON_WIN_ODDS"
        elif str(status).strip().lower() not in ALLOWED:
            reason = "UNKNOWN_OR_FINAL_STATUS"
        elif str(source).strip().lower() in {"", "none", "unknown"}:
            reason = "UNKNOWN_SOURCE"
        elif not obs or not got or obs > got:
            reason = "INVALID_TIMESTAMP"
        elif number < 1 or number > 99 or not math.isfinite(price) or price < 1:
            reason = "INVALID_HORSE_OR_PRICE"
        elif cutoff is not None and (obs > cutoff or got > cutoff):
            reason = "FUTURE_AS_OF"
        elif rid in starts and (obs >= starts[rid] or got >= starts[rid]):
            reason = "AFTER_RACE_START"
        if reason:
            reasons[reason] += 1
            continue
        stats = race_stats[rid]
        stats["eligible_rows"] += 1
        stats["horses"].add(number)
        stats["observations"].add(obs.isoformat())
        if str(status).strip().lower() == "manual_prerace":
            stats["manual_rows"] += 1
    result.update({
        "status": "AUDITED",
        "race_start_coverage": "provided" if starts else "not_provided",
        "race_start_times_checked": len(starts),
        "total_rows": len(rows),
        "eligible_rows": sum(v["eligible_rows"] for v in race_stats.values()),
        "rejected": dict(sorted(reasons.items())),
        "races": [
            {"race_id": rid, "eligible_rows": v["eligible_rows"],
             "distinct_horses": len(v["horses"]),
             "distinct_observation_times": len(v["observations"]),
             "manual_uncertified_rows": v["manual_rows"],
             "bet_status": "NO_BET"}
            for rid, v in sorted(race_stats.items())
        ],
    })
    return result


def main():
    parser = argparse.ArgumentParser(description="Read-only offline pre-race odds quality audit")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--as-of-time", help="Timezone-aware audit cutoff")
    parser.add_argument("--race-starts-json", help="Optional JSON object mapping race IDs to timezone-aware start times")
    parser.add_argument("--out", help="Optional report JSON path")
    args = parser.parse_args()
    starts = json.loads(Path(args.race_starts_json).read_text(encoding="utf-8")) if args.race_starts_json else {}
    if not isinstance(starts, dict):
        raise ValueError("race-starts-json must contain a JSON object")
    result = audit_odds_history(args.db, as_of_time=args.as_of_time, race_starts=starts)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
