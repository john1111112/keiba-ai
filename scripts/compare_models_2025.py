"""Compare AI-alone, market odds, and blended models on 2025 real data.

Strictly zero-cost, read-only DB access, no 2026 holdout access.
Evaluates Log Loss, Brier Score, and Top-1 Accuracy across models:
1. Uniform Baseline: 1 / N_runners
2. AI Alone: Calibrated softmax over age, weight_carry, and horse_number
3. Market Odds: Margin-adjusted market probabilities (q_i = 1/odds_i normalized)
4. Blended Models: Linear combination of AI and Market probabilities
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.market import compute_market_probabilities_array
from scripts.validation_weight_tuning import _probabilities

DEFAULT_AI_WEIGHTS = {"age": -0.25, "weight_carry": 0.1, "horse_number": 0.0}
DEFAULT_FEATURES = ("age", "weight_carry", "horse_number")


def load_validation_races_with_odds(
    connection: sqlite3.Connection,
    *,
    start_date: str = "2025-01-01",
    end_date: str = "2025-12-31",
    max_races: Optional[int] = None,
) -> List[List[Dict[str, Any]]]:
    """Load and validate races within the specified historical period with odds.

    STRICT SAFETY RULE: Never query or access data outside start_date and end_date.
    2026 holdout data access is strictly forbidden.
    """
    if end_date > "2025-12-31":
        raise ValueError(f"Access to post-2025 data ({end_date}) is strictly forbidden.")
    if start_date > end_date:
        raise ValueError("start_date must be earlier than or equal to end_date.")

    query = """
        SELECT r.race_id, r.date, res.horse_number, res.age,
               res.weight_carry, res.odds, res.finish_order
        FROM races r
        JOIN race_results res ON r.race_id = res.race_id
        WHERE r.date >= ? AND r.date <= ?
        ORDER BY r.date, r.race_id, res.horse_number
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    invalid_races = set()

    for race_id, date, number, age, carry, odds, finish in connection.execute(query, (start_date, end_date)):
        if (
            not race_id
            or not date
            or number is None
            or age is None
            or carry is None
            or odds is None
            or finish is None
        ):
            if race_id is not None:
                invalid_races.add(str(race_id))
            continue

        try:
            n = int(number)
            age_num = float(age)
            carry_num = float(carry)
            odds_num = float(odds)
        except (ValueError, TypeError):
            invalid_races.add(str(race_id))
            continue

        if n < 1 or not 0 < age_num < 30 or not 20 < carry_num < 90 or odds_num < 1.0 or not math.isfinite(odds_num):
            invalid_races.add(str(race_id))
            continue

        finish_str = str(finish).strip()
        if not finish_str.isdigit() or int(finish_str) < 1:
            invalid_races.add(str(race_id))
            continue

        row = {
            "race_id": str(race_id),
            "race_date": str(date),
            "horse_id": f"{race_id}:{n}",
            "horse_number": n,
            "odds": odds_num,
            "win": 1 if int(finish_str) == 1 else 0,
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


def evaluate_models_on_races(
    races: List[List[Dict[str, Any]]],
    ai_weights: Optional[Dict[str, float]] = None,
    features: Tuple[str, ...] = DEFAULT_FEATURES,
    blend_weights: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Compute Log Loss, Brier Score, and Top-1 Accuracy across models."""
    if not races:
        raise ValueError("Races list is empty")

    if ai_weights is None:
        ai_weights = DEFAULT_AI_WEIGHTS

    if blend_weights is None:
        blend_weights = [0.5, 0.3, 0.1]

    # Model specifications
    model_keys = ["uniform", "ai_alone", "market_odds"]
    for bw in blend_weights:
        model_keys.append(f"blend_{int(bw*100)}ai_{int((1-bw)*100)}mkt")

    stats = {
        m: {
            "race_log_loss_sum": 0.0,
            "runner_log_loss_sum": 0.0,
            "brier_sum": 0.0,
            "top1_hits": 0,
        }
        for m in model_keys
    }

    total_races = len(races)
    total_runners = sum(len(r) for r in races)

    for race in races:
        n = len(race)
        winner_index = next(i for i, r in enumerate(race) if r["win"] == 1)

        # 1. Uniform
        p_uniform = np.array([1.0 / n] * n)

        # 2. AI Alone
        p_ai = np.array(_probabilities(race, ai_weights, features))

        # 3. Market Odds
        odds_arr = np.array([r["odds"] for r in race])
        p_market = compute_market_probabilities_array(odds_arr)

        pred_map = {
            "uniform": p_uniform,
            "ai_alone": p_ai,
            "market_odds": p_market,
        }

        # 4. Blends
        for bw in blend_weights:
            b_key = f"blend_{int(bw*100)}ai_{int((1-bw)*100)}mkt"
            pred_map[b_key] = bw * p_ai + (1.0 - bw) * p_market

        for m_name, probs in pred_map.items():
            eps = 1e-15
            p_w = max(probs[winner_index], eps)
            stats[m_name]["race_log_loss_sum"] -= math.log(p_w)

            # Runner binary cross-entropy log loss
            for i, p in enumerate(probs):
                y = 1 if i == winner_index else 0
                p_c = np.clip(p, eps, 1.0 - eps)
                stats[m_name]["runner_log_loss_sum"] -= y * math.log(p_c) + (1 - y) * math.log(1.0 - p_c)
                stats[m_name]["brier_sum"] += (p - y) ** 2

            # Top-1 Accuracy: deterministic argmax with tie-break
            top1_cand = int(np.argmax(probs))
            if top1_cand == winner_index:
                stats[m_name]["top1_hits"] += 1

    results = {}
    for m_name in model_keys:
        results[m_name] = {
            "race_log_loss": round(float(stats[m_name]["race_log_loss_sum"] / total_races), 6),
            "runner_log_loss": round(float(stats[m_name]["runner_log_loss_sum"] / total_runners), 6),
            "runner_brier_score": round(float(stats[m_name]["brier_sum"] / total_runners), 6),
            "top1_accuracy": round(float(stats[m_name]["top1_hits"] / total_races), 6),
            "top1_hits": int(stats[m_name]["top1_hits"]),
            "total_races": int(total_races),
        }

    return {
        "models": results,
        "sample_counts": {
            "total_races": total_races,
            "total_runners": total_runners,
            "mean_runners_per_race": round(total_runners / total_races, 2),
        },
    }


def run_comparison(
    db_path: str,
    *,
    year: int = 2025,
    max_races: Optional[int] = None,
    ai_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Execute model comparison on historical real data for the given year."""
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"

    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")

    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        races = load_validation_races_with_odds(
            connection,
            start_date=start_date,
            end_date=end_date,
            max_races=max_races,
        )

    eval_result = evaluate_models_on_races(races, ai_weights=ai_weights)

    report_payload = {
        "status": "HISTORICAL_VALIDATION_MODEL_COMPARISON_COMPLETED",
        "evaluation_period": {
            "year": year,
            "start_date": start_date,
            "end_date": end_date,
        },
        "sample_counts": eval_result["sample_counts"],
        "models": eval_result["models"],
        "ai_weights": ai_weights or DEFAULT_AI_WEIGHTS,
        "holdout_status": "NOT_ACCESSED",
        "holdout_2026_isolated": True,
        "asof_status": "NOT_CERTIFIED",
        "data_status": "LOCAL_HISTORICAL_CACHE_PROVENANCE_UNVERIFIED",
        "roi": None,
        "warning": (
            "Odds in local SQLite are final settlement odds; certified pre-race timestamps are not established. "
            "2026 holdout labels were not queried or accessed. This evaluation does not imply profitability in live markets."
        ),
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # Add hash
    json_bytes = json.dumps(report_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    report_payload["sha256"] = hashlib.sha256(json_bytes).hexdigest()

    return report_payload


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/keiba.db", help="Path to keiba.db")
    parser.add_argument("--year", type=int, default=2025, help="Validation year (strictly <= 2025)")
    parser.add_argument("--max-races", type=int, default=None, help="Maximum races to process")
    parser.add_argument("--out", default=None, help="Output path for JSON report")
    args = parser.parse_args(argv)

    if args.year > 2025:
        parser.error("Accessing data past 2025 is strictly prohibited.")

    report = run_comparison(args.db, year=args.year, max_races=args.max_races)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(f"Comparison report saved to {out_path}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
