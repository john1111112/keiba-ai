"""Seal prediction artifacts before a separately authorized evaluator sees outcomes.

This tool never loads race results or computes performance. SHA-256 seals are
tamper-evidence, NOT access controls: keep holdout labels outside the agent's
workspace, credentials, repo, logs, and tools.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def seal(predictions, *, git_commit, model_sha256, train_end, validation_end, test_start, test_end, policy):
    if not (train_end < validation_end < test_start <= test_end):
        raise ValueError("Invalid chronological split")
    if not predictions or not isinstance(predictions, list):
        raise ValueError("Predictions must be a nonempty list")
    seen = set()
    forbidden = {"finish_order", "win", "winner", "payout", "actual_result", "result", "final_odds", "roi"}
    for row in predictions:
        if not isinstance(row, dict) or not row.get("race_id") or not row.get("horse_id"):
            raise ValueError("Each prediction requires race_id and horse_id")
        if forbidden.intersection(row):
            raise ValueError("Outcome or post-race field present in prediction artifact")
        key = (row["race_id"], row["horse_id"])
        if key in seen:
            raise ValueError("Duplicate race/horse")
        seen.add(key)
    if not git_commit or not model_sha256 or not isinstance(policy, dict):
        raise ValueError("Commit, model digest, and policy required")
    artifact = {
        "protocol": "sealed-paper-evaluation-v1",
        "git_commit": git_commit,
        "model_sha256": model_sha256,
        "split": {"train_end": train_end, "validation_end": validation_end,
                  "test_start": test_start, "test_end": test_end},
        "selection_policy": policy,
        "predictions": predictions,
        "evaluation_status": "SEALED_AWAITING_EXTERNAL_HOLDOUT",
        "roi": None,
    }
    return {"artifact": artifact, "sha256": hashlib.sha256(canonical_bytes(artifact)).hexdigest()}


def verify(sealed):
    if not isinstance(sealed, dict) or "artifact" not in sealed or "sha256" not in sealed:
        return False
    return hashlib.sha256(canonical_bytes(sealed["artifact"])).hexdigest() == sealed["sha256"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--predictions", required=True, help="JSON list of prediction records WITHOUT outcomes")
    p.add_argument("--output", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--model-sha256", required=True)
    p.add_argument("--train-end", required=True)
    p.add_argument("--validation-end", required=True)
    p.add_argument("--test-start", required=True)
    p.add_argument("--test-end", required=True)
    p.add_argument("--policy", required=True, help="Frozen selection policy JSON file")
    args = p.parse_args()
    rows = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    result = seal(rows, git_commit=args.commit, model_sha256=args.model_sha256,
                  train_end=args.train_end, validation_end=args.validation_end,
                  test_start=args.test_start, test_end=args.test_end, policy=policy)
    output = Path(args.output)
    if output.exists():
        raise FileExistsError("Refusing to overwrite existing seal")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["sha256"])


if __name__ == "__main__":
    main()
