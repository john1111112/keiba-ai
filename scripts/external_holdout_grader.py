"""Independent, paper-only grader for sealed holdout predictions.

RUN THIS OUTSIDE ANTIGRAVITY'S WORKSPACE. Keep outcomes and this run's
output inaccessible to the model developer until the model is retired.
No market odds/ROI computation is performed.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from scripts.seal_holdout_predictions import verify


def grade(sealed: dict, outcomes: list[dict]) -> dict:
    if not verify(sealed):
        raise ValueError("Prediction seal mismatch")
    artifact = sealed["artifact"]
    if artifact.get("evaluation_status") != "SEALED_AWAITING_EXTERNAL_HOLDOUT":
        raise ValueError("Unexpected artifact state")
    predictions = artifact["predictions"]
    if not isinstance(outcomes, list):
        raise ValueError("Outcomes must be a list")
    actual = {}
    for row in outcomes:
        if not isinstance(row, dict) or not row.get("race_id") or not row.get("horse_id"):
            raise ValueError("Malformed outcome")
        key = (row["race_id"], row["horse_id"])
        if key in actual or type(row.get("win")) is not int or row["win"] not in (0, 1):
            raise ValueError("Duplicate or invalid outcome")
        actual[key] = row["win"]

    by_race = {}
    keys = set()
    for row in predictions:
        key = (row["race_id"], row["horse_id"])
        if key in keys:
            raise ValueError("Duplicate prediction")
        keys.add(key)
        p = row.get("predicted_win_probability")
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("Invalid probability")
        by_race.setdefault(row["race_id"], []).append(row)

    if set(actual) != keys:
        raise ValueError("Predictions and outcome runners must match exactly")
    for rid, rows in by_race.items():
        if abs(sum(row["predicted_win_probability"] for row in rows) - 1) > 1e-6:
            raise ValueError("Per-race probability sum must equal one")
        if sum(actual[(rid, row["horse_id"])] for row in rows) != 1:
            raise ValueError("Race must have exactly one winner")

    n = len(predictions)
    brier = sum((row["predicted_win_probability"] - actual[(row["race_id"], row["horse_id"])]) ** 2 for row in predictions) / n
    log_loss = -sum(
        actual[(row["race_id"], row["horse_id"])] * math.log(max(row["predicted_win_probability"], 1e-15))
        for row in predictions
    ) / len(by_race)
    top1 = sum(
        actual[(rid, max(rows, key=lambda r: r["predicted_win_probability"])["horse_id"])]
        for rid, rows in by_race.items()
    )
    candidates = [row for row in predictions if row.get("status") == "RESEARCH_CANDIDATE"]
    candidate_hits = sum(actual[(row["race_id"], row["horse_id"])] for row in candidates)
    return {
        "status": "INDEPENDENT_PAPER_EVALUATION",
        "sealed_prediction_sha256": sealed["sha256"],
        "race_count": len(by_race),
        "runner_count": n,
        "top1_hits": top1,
        "top1_accuracy": top1 / len(by_race),
        "brier": brier,
        "race_log_loss": log_loss,
        "candidate_count": len(candidates),
        "candidate_hits": candidate_hits,
        "candidate_hit_rate": candidate_hits / len(candidates) if candidates else None,
        "roi": None,
        "roi_status": "NOT_EVALUABLE_WITHOUT_VERIFIED_DECISION_TIME_ODDS_AND_PAYOUTS",
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sealed", required=True)
    p.add_argument("--outcomes", required=True, help="Keep this file outside developer/agent workspace")
    p.add_argument("--output", required=True)
    args = p.parse_args()
    sealed = json.loads(Path(args.sealed).read_text(encoding="utf-8"))
    outcomes = json.loads(Path(args.outcomes).read_text(encoding="utf-8"))
    result = grade(sealed, outcomes)
    output = Path(args.output)
    if output.exists():
        raise FileExistsError("Refusing to overwrite prior holdout report")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
