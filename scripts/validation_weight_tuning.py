"""Deterministic validation-only linear weight tuning for paper research.

Input must be prepared from training/validation periods only. This module has no
test-label argument and cannot read a database. No claim of holdout performance.
"""
from __future__ import annotations
import hashlib
import itertools
import json
import math
from typing import Any


def _probabilities(rows: list[dict], weights: dict[str, float], features: tuple[str, ...]) -> list[float]:
    scores = [sum(float(r["features"][f]) * weights[f] for f in features) for r in rows]
    pivot = max(scores)
    exp_scores = [math.exp(s - pivot) for s in scores]
    total = sum(exp_scores)
    return [s / total for s in exp_scores]


def _validate(races: list[list[dict]], features: tuple[str, ...], split: str) -> None:
    if not races:
        raise ValueError(f"{split} must contain races")
    for race in races:
        if len(race) < 2 or sum(r.get("win") == 1 for r in race) != 1:
            raise ValueError(f"{split}: race must have exactly one winner and >=2 runners")
        ids = [r.get("horse_id") for r in race]
        if any(not h for h in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"{split}: missing or duplicate horse IDs")
        for row in race:
            if row.get("win") not in (0, 1) or isinstance(row["win"], bool):
                raise ValueError("win must be integer 0 or 1")
            if not isinstance(row.get("features"), dict):
                raise ValueError("missing feature map")
            for f in features:
                v = row["features"].get(f)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError(f"invalid feature {f}")


def tune_weights(train_races: list[list[dict]], validation_races: list[list[dict]],
                 *, features: tuple[str, ...], weight_grid: tuple[float, ...] = (-1.0, 0.0, 1.0),
                 max_combinations: int = 10000) -> dict[str, Any]:
    """Grid search on validation labels; test/holdout labels are never accepted.

    Input features MUST be computed as-of each historical race and normalized
    with TRAIN-only statistics. Caller must enforce race-date split and embargo.
    """
    if not features or len(set(features)) != len(features):
        raise ValueError("features must be nonempty and unique")
    if not weight_grid or any(not math.isfinite(x) for x in weight_grid):
        raise ValueError("weight grid must be finite and nonempty")
    if len(weight_grid) ** len(features) > max_combinations:
        raise ValueError("grid too large")
    _validate(train_races, features, "train")
    _validate(validation_races, features, "validation")
    train_ids = {r.get("race_id") for race in train_races for r in race}
    val_ids = {r.get("race_id") for race in validation_races for r in race}
    if None in train_ids or None in val_ids or train_ids & val_ids:
        raise ValueError("race IDs missing or overlap across splits")
    # Caller provides chronological split; dates are required and validated here.
    def bounds(races):
        dates = []
        for race in races:
            ds = {r.get("race_date") for r in race}
            if len(ds) != 1 or not isinstance(next(iter(ds)), str):
                raise ValueError("race_date missing or inconsistent")
            date = next(iter(ds))
            from datetime import date as Date
            Date.fromisoformat(date)
            dates.append(date)
        return min(dates), max(dates)
    _, train_end = bounds(train_races)
    val_start, val_end = bounds(validation_races)
    if train_end >= val_start:
        raise ValueError("training must strictly precede validation")
    best = None
    for values in itertools.product(weight_grid, repeat=len(features)):
        weights = dict(zip(features, values))
        total_loss = 0.0
        count = 0
        for race in validation_races:
            probs = _probabilities(race, weights, features)
            for row, p in zip(race, probs):
                if row["win"] == 1:
                    total_loss -= math.log(max(p, 1e-15))
                    count += 1
        loss = total_loss / count
        if best is None or loss < best["validation_log_loss"]:
            best = {"weights": weights, "validation_log_loss": loss}
    result = {
        "status": "VALIDATION_ONLY_NOT_HOLDOUT",
        "features": list(features),
        "train_races": len(train_races),
        "validation_races": len(validation_races),
        "train_end": train_end,
        "validation_start": val_start,
        "validation_end": val_end,
        "grid_size": len(weight_grid) ** len(features),
        "selection": best,
        "holdout_evaluated": False,
        "roi": None,
    }
    result["sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
