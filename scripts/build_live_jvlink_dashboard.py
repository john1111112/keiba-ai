"""Read-only, provenance-checked AI predictions for live JV-Link race feeds.

Unlike build_race_dashboard (which reads archival envelopes), this module
consumes the exact direct JSON emitted by JVLinkClient.process_records_to_feed.
Never uses mock races, manufactured odds, or sends notifications or bets.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.automated_monitor import validate_feed_payload, load_alert_config
from src.model import KeibaAIWinModel, MODEL_WIN_V2_28_PATH
from src.prerace_feature_pipeline import build_prerace_28features


def build_live_dashboard(
    feed_path,
    *,
    now: Optional[datetime] = None,
    model_path=None,
    past_db_path=None,
    max_odds_age_seconds: Optional[int] = None,
):
    """Build rows from a valid, signed JV-Link acquisition without side effects."""
    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        raise ValueError("Dashboard time must be timezone aware")
    path = Path(feed_path)
    result = {
        "status": "NOT_READY",
        "as_of": when.isoformat(),
        "source_provenance_certified": False,
        "rows": [],
        "races": 0,
        "runners": 0,
        "diagnostics": [],
        "bet_status": "NO_BET",
    }
    if not path.is_file():
        result["diagnostics"].append("Live JV-Link feed file not found")
        return result
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result["status"] = "INVALID_FEED"
        result["diagnostics"].append(str(exc))
        return result

    valid, reason, races, certified = validate_feed_payload(
        data, when, feed_path=path
    )
    if not valid:
        result["status"] = "INVALID_FEED"
        result["diagnostics"].append(reason)
        return result
    if not certified:
        result["status"] = "UNVERIFIED_SOURCE"
        result["diagnostics"].append("JV-Link provenance signature not verified")
        return result
    result["source_provenance_certified"] = True
    result["races"] = len(races)
    result["runners"] = sum(len(race["horses"]) for race in races)
    if not races:
        result["status"] = "NO_UPCOMING_RACES"
        return result

    try:
        model = KeibaAIWinModel(model_path=Path(model_path or MODEL_WIN_V2_28_PATH))
        model.load()
    except Exception as exc:
        result["status"] = "MODEL_UNAVAILABLE"
        result["diagnostics"].append(f"Model not verified: {exc}")
        return result

    config = load_alert_config()
    age_limit = (
        int(max_odds_age_seconds)
        if max_odds_age_seconds is not None
        else int(config.get("max_odds_age_seconds", 120))
    )
    if age_limit <= 0:
        raise ValueError("max_odds_age_seconds must be positive")
    problems = 0

    for race in races:
        race_id = str(race["race_id"])
        horses = race["horses"]
        race_info = {
            name: race.get(name) for name in (
                "race_id", "race_date", "race_course", "surface",
                "distance", "condition", "direction"
            )
        }
        try:
            features = build_prerace_28features(
                race_info, horses, past_db_path=past_db_path
            )
            predictions = model.predict_win_race(
                features, normalize_to_one=True
            )
            values = {}
            for _, row in predictions.iterrows():
                number = int(row["horse_number"])
                probability = float(row["pred_win_prob"])
                if not math.isfinite(probability) or not 0 <= probability <= 1:
                    raise ValueError("Model returned invalid win probability")
                values[number] = probability
            if len(values) != len(horses):
                raise ValueError("Model did not return one probability per runner")
        except Exception as exc:
            problems += 1
            result["diagnostics"].append(f"{race_id}: inference unavailable ({exc})")
            continue

        for horse in horses:
            number = int(horse["horse_number"])
            probability = values[number]
            odds = horse.get("win_odds")
            observed = horse.get("odds_observed_at")
            observation_status = "MISSING_ODDS"
            live_odds = None
            expected_value = None
            if odds is not None and observed:
                try:
                    price = float(odds)
                    observed_at = datetime.fromisoformat(
                        str(observed).replace("Z", "+00:00")
                    )
                    age = (when - observed_at).total_seconds()
                    if (observed_at.tzinfo is not None
                            and math.isfinite(price) and price >= 1.0
                            and 0 <= age <= age_limit):
                        live_odds = price
                        expected_value = round(probability * price, 4)
                        observation_status = "CURRENT_ODDS"
                    else:
                        observation_status = "STALE_OR_INVALID_ODDS"
                except (ValueError, TypeError):
                    observation_status = "INVALID_ODDS_TIME"

            result["rows"].append({
                "race_id": race_id,
                "race_time": race["race_time"],
                "horse_number": number,
                "horse_name": horse.get("horse_name", ""),
                "win_odds": live_odds,
                "odds_observed_at": observed,
                "win_probability": probability,
                "expected_value": expected_value,
                "recommendation": (
                    "参考（有効オッズ）" if expected_value is not None
                    else "オッズ未取得・更新待ち"
                ),
                "odds_status": observation_status,
            })
    result["rows"].sort(
        key=lambda row: (
            row["expected_value"] is None,
            -(row["expected_value"] or 0.0),
            row["race_time"], row["race_id"], row["horse_number"]
        )
    )
    result["status"] = (
        "INFERENCE_PARTIAL" if problems else "VERIFIED_LIVE_READ_ONLY"
    )
    return result
