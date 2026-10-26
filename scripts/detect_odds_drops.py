"""Offline win-odds drop alerts from verified, already-recorded pre-race snapshots.

No network calls, no betting, no outcome labels. Missing or unverified timestamps
are excluded; a drop is an observation, NOT evidence of a large bettor.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path


def parse_time(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None  # timezone provenance must be explicit
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def detect_drops(db_path, *, race_id=None, threshold_pct=30.0, max_gap_minutes=30, as_of_time=None):
    """Compare each horse's latest two valid snapshots, not settled odds."""
    if threshold_pct <= 0 or max_gap_minutes <= 0:
        raise ValueError("threshold and max gap must be positive")
    cutoff = parse_time(as_of_time) if as_of_time is not None else None
    if as_of_time is not None and cutoff is None:
        raise ValueError("as_of_time must be an ISO 8601 timestamp with timezone")
    db_path = Path(db_path)
    if not db_path.is_file():
        return {"status": "NO_ODDS_DB", "alerts": [], "compared_horses": 0}
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute("""
            SELECT race_id, horse_number, odds_value, observed_at, fetched_at,
                   data_status, source, timing_tag
            FROM odds_timeline
            WHERE odds_type = 'win'
            ORDER BY race_id, horse_number, id
        """).fetchall()
    except sqlite3.OperationalError:
        return {"status": "NO_ODDS_TABLE", "alerts": [], "compared_horses": 0}
    finally:
        conn.close()

    grouped = {}
    rejected = 0
    for rid, horse, odds, observed, fetched, status, source, timing in rows:
        if race_id is not None and str(rid) != str(race_id):
            continue
        if str(status).lower() in {"final", "final_settled", "post_race", "settled", "result"}:
            rejected += 1
            continue
        obs, got = parse_time(observed), parse_time(fetched)
        try:
            price = float(odds)
        except (ValueError, TypeError):
            rejected += 1
            continue
        if not obs or not got or obs > got or (cutoff is not None and (obs > cutoff or got > cutoff)) or not math.isfinite(price) or price < 1:
            rejected += 1
            continue
        grouped.setdefault((str(rid), int(horse)), []).append((obs, got, price, source, timing))

    alerts, compared = [], 0
    # Market-wide reference: other horses in the same race and publication window.
    market_changes = {}
    for (rid, horse), points in grouped.items():
        by_time = {}
        for p in points:
            if p[0] not in by_time or p[1] < by_time[p[0]][1]:
                by_time[p[0]] = p
        ordered = sorted(by_time.values(), key=lambda x: x[0])
        if len(ordered) < 2:
            continue
        prev, curr = ordered[-2:]
        gap = (curr[0] - prev[0]).total_seconds() / 60
        if 0 < gap <= max_gap_minutes:
            market_changes[(rid, horse)] = (prev[0], curr[0], (prev[2] - curr[2]) / prev[2] * 100)
    for (rid, horse), points in sorted(grouped.items()):
        # Identical publication times are not distinct market updates.
        distinct = {}
        for p in points:
            if p[0] not in distinct or p[1] < distinct[p[0]][1]:
                distinct[p[0]] = p
        ordered = sorted(distinct.values(), key=lambda x: x[0])
        if len(ordered) < 2:
            continue
        previous, latest = ordered[-2:]
        minutes = (latest[0] - previous[0]).total_seconds() / 60
        if minutes <= 0 or minutes > max_gap_minutes:
            continue
        compared += 1
        drop = (previous[2] - latest[2]) / previous[2] * 100
        if drop >= threshold_pct:
            peers = [change for (peer_rid, peer_horse), (peer_prev, peer_curr, change) in market_changes.items()
                     if peer_rid == rid and peer_horse != horse and peer_prev == previous[0] and peer_curr == latest[0]]
            peer_avg = sum(peers) / len(peers) if peers else None
            alerts.append({
                "race_id": rid, "horse_number": horse,
                "previous_odds": previous[2], "latest_odds": latest[2],
                "drop_pct": round(drop, 2), "gap_minutes": round(minutes, 2),
                "peer_count": len(peers),
                "peer_avg_drop_pct": round(peer_avg, 2) if peer_avg is not None else None,
                "relative_drop_pct_points": round(drop - peer_avg, 2) if peer_avg is not None else None,
                "previous_observed_at": previous[0].isoformat(),
                "latest_observed_at": latest[0].isoformat(),
                "signal": "ODDS_DROP_ONLY_NOT_PROOF_OF_LARGE_BET"
            })
    return {"status": "OK", "compared_horses": compared,
            "rejected_records": rejected, "alerts": alerts}


def main():
    parser = argparse.ArgumentParser(description="Offline pre-race odds drop audit (no paid API)")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--race-id")
    parser.add_argument("--as-of-time", help="Only use odds published AND fetched by this timezone-aware time")
    parser.add_argument("--threshold-pct", type=float, default=30)
    parser.add_argument("--max-gap-minutes", type=float, default=30)
    parser.add_argument("--out")
    args = parser.parse_args()
    result = detect_drops(args.db, race_id=args.race_id,
                          threshold_pct=args.threshold_pct,
                          max_gap_minutes=args.max_gap_minutes, as_of_time=args.as_of_time)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
