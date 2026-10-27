"""Pre-race, source-gated interaction signals. No prediction or betting actions.

This module refuses to use horse numbers as physical starting-stall positions.
It never reads the quarantined historic database and never imputes unknown facts.
Any synthetic/unit-test fixture is NOT evidence of real JRA-VAN correctness.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean


class InsufficientPreRaceEvidence(ValueError):
    """Cannot construct this signal without verified official prior observations."""


def observed_utc(value):
    if not isinstance(value, str):
        raise InsufficientPreRaceEvidence("Missing timezone-aware source timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InsufficientPreRaceEvidence("Invalid source timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise InsufficientPreRaceEvidence("Timezone-aware timestamp required")
    return dt.astimezone(timezone.utc)


def _history(horse, as_of):
    """Only official RESULT rows with a capture time strictly earlier than the decision."""
    valid = []
    for run in horse.get("prior_runs", []):
        if run.get("source") != "JRA-VAN" or run.get("result_verified") is not True:
            continue
        when = observed_utc(run["race_time_utc"])
        seen = observed_utc(run["result_received_at_utc"])
        if not (when <= seen <= as_of):
            continue
        n, finish = run.get("field_size"), run.get("finish")
        if type(n) is not int or type(finish) is not int or not (5 <= n <= 28 and 1 <= finish <= n):
            continue
        corner = run.get("first_corner")
        if type(corner) is not int or not 1 <= corner <= n:
            corner = None
        valid.append((when, (n-finish)/(n-1),
                      (n-corner)/(n-1) if corner is not None else None))
    valid.sort(key=lambda r: r[0])
    recent = valid[-3:]
    if len(recent) < 3:
        return {"form": None, "early_pace": None, "past_count": len(recent)}
    known_pace = [r[2] for r in recent if r[2] is not None]
    return {
        "form": round(mean(r[1] for r in recent), 6),
        "early_pace": round(mean(known_pace), 6) if len(known_pace) >= 2 else None,
        "past_count": len(recent),
    }


def build_interaction_signals(race, entrants, as_of_utc):
    """Relationships across a full verified race field, never outcome-informed.

    Actual physical stall assignments are optional; when their independent
    verification is unavailable, adjacent-stall effects are explicitly null.
    The resulting signals cannot authorize model training or live alerts.
    """
    as_of = observed_utc(as_of_utc)
    post = observed_utc(race["post_time_utc"])
    race_seen = observed_utc(race["captured_at_utc"])
    if as_of >= post or race_seen > as_of:
        raise InsufficientPreRaceEvidence("Race snapshot unavailable before off")
    if race.get("source") != "JRA-VAN" or race.get("complete_field_verified") is not True:
        raise InsufficientPreRaceEvidence("Full JRA-VAN field was not independently verified")
    n = race.get("declared_field_size")
    if type(n) is not int or len(entrants) != n or not (5 <= n <= 28):
        raise InsufficientPreRaceEvidence("Partial or invalid full-field entry list")
    by_id, by_num, by_gate = {}, {}, {}
    actual_gate_verified = race.get("physical_gate_assignment_verified") is True
    for e in entrants:
        hid = e.get("horse_id")
        num = e.get("horse_number")
        if (not isinstance(hid, str) or not hid or type(num) is not int
                or not 1 <= num <= 28 or e.get("source") != "JRA-VAN"
                or observed_utc(e["captured_at_utc"]) > as_of):
            raise InsufficientPreRaceEvidence("Unproven runner identity or observation")
        if hid in by_id or num in by_num:
            raise InsufficientPreRaceEvidence("Duplicate horse ID or number")
        if e.get("sex") not in ("牡", "牝", "セン", None):
            raise InsufficientPreRaceEvidence("Unrecognized official sex code")
        by_id[hid], by_num[num] = e, e
        if actual_gate_verified:
            slot = e.get("physical_gate_slot")
            if type(slot) is not int or not 1 <= slot <= n or slot in by_gate:
                raise InsufficientPreRaceEvidence("Invalid independently verified gate slot")
            by_gate[slot] = e
    form = {hid: _history(e, as_of) for hid, e in by_id.items()}
    result = []
    for hid, e in by_id.items():
        others = [other for oid, other in by_id.items() if oid != hid]
        mine = form[hid]
        all_forms = mine["form"] is not None and all(form[x["horse_id"]]["form"] is not None for x in others)
        all_paces = mine["early_pace"] is not None and all(form[x["horse_id"]]["early_pace"] is not None for x in others)
        stronger = (sum(form[x["horse_id"]]["form"] > mine["form"] + .1 for x in others)
                    if all_forms else None)
        faster = (sum(form[x["horse_id"]]["early_pace"] > mine["early_pace"] + .1 for x in others)
                  if all_paces else None)
        near_mare = None
        neighbors = []
        if actual_gate_verified:
            slot = e["physical_gate_slot"]
            neighbors = [by_gate[k] for k in (slot-1, slot+1) if k in by_gate]
            if e.get("sex") == "牡" and all(x.get("sex") is not None for x in neighbors):
                near_mare = any(x.get("sex") == "牝" for x in neighbors)
        result.append({
            "horse_id": hid,
            "horse_number": e["horse_number"],
            "prior_form": mine["form"],
            "prior_early_pace": mine["early_pace"],
            "opponents_better_prior_form": stronger,
            "opponents_faster_early": faster,
            "physical_stall_neighbor_mare_hypothesis": near_mare,
            "physical_gate_assignment_verified": actual_gate_verified,
            "independently_certified_prediction": False,
            "is_bet_recommendation": False,
        })
    return result
