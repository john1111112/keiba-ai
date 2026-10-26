"""Read-only upcoming race dashboard from validated licensed-feed snapshots.

Never invent probabilities, EV, or a certified source. No SDK/network access.
"""
from __future__ import annotations
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from scripts.fetch_authorized_races import validate_feed, timestamp


def build_dashboard(snapshot_path, *, now=None, predictions=None):
    current = now or datetime.now(timezone.utc).isoformat()
    raw = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"received_at", "feed", "provenance_certified"}:
        raise ValueError("Invalid snapshot envelope")
    received = timestamp(raw["received_at"])
    if received > timestamp(current):
        raise ValueError("Snapshot received in the future")
    validate_feed(raw["feed"], received_at=current)
    if timestamp(raw["feed"]["published_at"]) > received:
        raise ValueError("Feed publication after receipt")
    predictions = predictions or {}
    rows = []
    for race in raw["feed"]["races"]:
        if race["race_status"] != "upcoming":
            continue
        for horse in race["horses"]:
            odds = horse["win_odds"]
            key = (race["race_id"], horse["horse_number"])
            prob = predictions.get(key)
            if prob is not None and (type(prob) not in (int, float) or not math.isfinite(prob) or not 0 <= prob <= 1):
                raise ValueError("Invalid model probability")
            ev = round(float(prob) * odds, 6) if prob is not None and odds is not None else None
            rows.append({
                "race_id": race["race_id"], "race_time": race["race_time"],
                "horse_number": horse["horse_number"], "horse_name": horse["horse_name"],
                "win_odds": odds, "odds_observed_at": horse["odds_observed_at"],
                "win_probability": prob, "expected_value": ev,
                "recommendation": "REVIEW_ONLY_UNCERTIFIED" if ev is not None else "UNAVAILABLE_MODEL_NOT_CERTIFIED",
            })
    rows.sort(key=lambda row: (row["expected_value"] is None, -(row["expected_value"] or 0), row["race_time"], row["race_id"], row["horse_number"]))
    return {"status": "READ_ONLY_UNCERTIFIED", "as_of": current,
            "source_provenance_certified": False,
            "races": len({row["race_id"] for row in rows}), "runners": len(rows),
            "rows": rows, "bet_status": "NO_BET"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only licensed-feed race dashboard")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    report = build_dashboard(args.snapshot)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
