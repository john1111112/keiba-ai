"""Run actual local DB weight selection on TRAIN and VALIDATION only.

No holdout race or outcome is queried. This is retrospective model development,
NOT an independently sealed test and NOT a certified as-of historical forecast.
Only immutable-looking attributes are used; their source timestamps remain
unverified. No odds, payouts, ROI, bets, or production writes.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

from scripts.validation_weight_tuning import tune_weights


def load_period(connection, *, start: str, end: str, max_races: int | None = None):
    """Dates inclusive. Never query outside the requested date range."""
    query = """
        SELECT r.race_id, r.date, res.horse_number, res.age,
               res.weight_carry, res.finish_order
        FROM races r
        JOIN race_results res ON r.race_id = res.race_id
        WHERE r.date >= ? AND r.date <= ?
        ORDER BY r.date, r.race_id, res.horse_number
    """
    grouped = {}
    invalid_races = set()
    for race_id, date, number, age, carry, finish in connection.execute(query, (start, end)):
        if not race_id or not date or number is None or age is None or carry is None:
            if race_id is not None:
                invalid_races.add(str(race_id))
            continue
        try:
            n = int(number)
            age_num = float(age)
            carry_num = float(carry)
        except (ValueError, TypeError):
            invalid_races.add(str(race_id))
            continue
        if n < 1 or not 0 < age_num < 30 or not 20 < carry_num < 90:
            invalid_races.add(str(race_id))
            continue
        row = {
            "race_id": str(race_id), "race_date": str(date),
            # Race-local runner ID only; never used to link horse histories.
            "horse_id": f"{race_id}:{n}",
            "win": 1 if str(finish).strip() == "1" else 0,
            "features": {
                "age": age_num - 4.5,
                "weight_carry": carry_num - 55.0,
                "horse_number": n - 8.0,
            },
        }
        grouped.setdefault(str(race_id), []).append(row)

    cleaned = []
    for rid, rows in grouped.items():
        if rid in invalid_races:
            continue
        if len(rows) < 2 or len({r["horse_id"] for r in rows}) != len(rows):
            continue
        if sum(r["win"] for r in rows) != 1:
            continue
        cleaned.append(rows)
        if max_races is not None and len(cleaned) >= max_races:
            break
    return cleaned


def run(db: str, *, train_start: str, train_end: str,
        validation_start: str, validation_end: str,
        max_races: int | None = None) -> dict:
    from datetime import date
    dates = [date.fromisoformat(d) for d in (
        train_start, train_end, validation_start, validation_end
    )]
    if not (dates[0] <= dates[1] < dates[2] <= dates[3]):
        raise ValueError("Train and validation must be ordered and non-overlapping")
    path = Path(db).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        train = load_period(connection, start=train_start, end=train_end, max_races=max_races)
        validation = load_period(connection, start=validation_start, end=validation_end,
                                 max_races=max_races)
    result = tune_weights(
        train, validation,
        features=("age", "weight_carry", "horse_number"),
        weight_grid=(-0.25, -0.1, 0.0, 0.1, 0.25),
    )
    # Untuned uniform reference over the exact same validation races.
    # This is NOT a market odds baseline and not unbiased holdout evidence.
    uniform_log_loss = sum(math.log(len(race)) for race in validation) / len(validation)
    weights = result["selection"]["weights"]
    top1_hits = 0
    for race in validation:
        scores = [sum(row["features"][name] * weights[name] for name in weights)
                  for row in race]
        predicted = max(range(len(race)), key=lambda i: scores[i])
        top1_hits += race[predicted]["win"]
    result["validation_comparison"] = {
        "reference": "UNIFORM_RANDOM_PROBABILITY_NOT_MARKET",
        "uniform_log_loss": uniform_log_loss,
        "selected_validation_log_loss": result["selection"]["validation_log_loss"],
        "selected_top1_accuracy": top1_hits / len(validation),
        "same_validation_races": True,
        "selection_bias_warning": (
            "Weights were chosen using these validation outcomes. This is "
            "NOT an unbiased holdout improvement estimate."
        ),
    }
    result["data_status"] = "LOCAL_HISTORICAL_CACHE_PROVENANCE_UNVERIFIED"
    result["asof_status"] = "NOT_CERTIFIED"
    result["holdout_status"] = "NOT_ACCESSED"
    result["roi"] = None
    result["note"] = (
        "Weights optimized on historical validation outcomes. This is development, "
        "not independent future prediction or real-money expected-value evidence."
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/keiba.db")
    parser.add_argument("--train-start", default="2022-01-01")
    parser.add_argument("--train-end", default="2024-12-31")
    parser.add_argument("--validation-start", default="2025-01-01")
    parser.add_argument("--validation-end", default="2025-12-31")
    parser.add_argument("--max-races", type=int, default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    if args.max_races is not None and args.max_races < 1:
        parser.error("--max-races must be positive")
    report = run(
        args.db, train_start=args.train_start, train_end=args.train_end,
        validation_start=args.validation_start, validation_end=args.validation_end,
        max_races=args.max_races,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        output = Path(args.out)
        if output.exists():
            raise FileExistsError("Refusing to overwrite prior tuning result")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
