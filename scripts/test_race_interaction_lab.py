"""Synthetic structural checks, not JRA-VAN real-data evidence."""
import copy
import unittest
from src.race_interaction_lab import (
    InsufficientPreRaceEvidence, build_interaction_signals,
)

AS_OF = "2026-10-27T09:00:00+00:00"

def fixture():
    race = {
        "source": "JRA-VAN", "complete_field_verified": True,
        "declared_field_size": 5, "captured_at_utc": "2026-10-27T08:00:00+00:00",
        "post_time_utc": "2026-10-28T08:00:00+00:00",
        "physical_gate_assignment_verified": False,
    }
    entrants = []
    for i in range(1, 6):
        runs = []
        for d in ("2026-03-01", "2026-04-01", "2026-05-01"):
            runs.append({
                "source": "JRA-VAN", "result_verified": True,
                "race_time_utc": d+"T10:00:00+00:00",
                "result_received_at_utc": d+"T11:00:00+00:00",
                "field_size": 12, "finish": i, "first_corner": i,
            })
        entrants.append({
            "source": "JRA-VAN", "captured_at_utc": "2026-10-27T08:00:00+00:00",
            "horse_id": "HORSE"+str(i), "horse_number": i,
            "sex": "牡" if i == 1 else "牝" if i == 2 else "セン",
            "prior_runs": runs,
        })
    return race, entrants


class InteractionResearchGateTests(unittest.TestCase):
    def test_official_verified_prior_results_produce_opponent_signals(self):
        race, entrants = fixture()
        rows = build_interaction_signals(race, entrants, AS_OF)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["opponents_better_prior_form"], 0)
        self.assertEqual(rows[4]["opponents_better_prior_form"], 3)
        self.assertIsNone(rows[0]["physical_stall_neighbor_mare_hypothesis"])
        self.assertFalse(rows[0]["is_bet_recommendation"])

    def test_actual_gate_verified_uses_physical_slot_not_horse_number(self):
        race, entrants = fixture()
        race["physical_gate_assignment_verified"] = True
        for i, horse in enumerate(entrants):
            horse["physical_gate_slot"] = i+1
        rows = build_interaction_signals(race, entrants, AS_OF)
        self.assertTrue(rows[0]["physical_stall_neighbor_mare_hypothesis"])
        self.assertTrue(rows[0]["physical_gate_assignment_verified"])

    def test_future_capture_cannot_be_prior_result(self):
        race, entrants = fixture()
        for horse in entrants:
            for run in horse["prior_runs"]:
                run["result_received_at_utc"] = "2026-10-28T11:00:00+00:00"
        rows = build_interaction_signals(race, entrants, AS_OF)
        self.assertIsNone(rows[0]["prior_form"])
        self.assertIsNone(rows[0]["opponents_better_prior_form"])

    def test_incomplete_roster_blocks(self):
        race, entrants = fixture()
        with self.assertRaises(InsufficientPreRaceEvidence):
            build_interaction_signals(race, entrants[:4], AS_OF)

    def test_duplicate_horse_id_blocks(self):
        race, entrants = fixture()
        entrants[1]["horse_id"] = entrants[0]["horse_id"]
        with self.assertRaises(InsufficientPreRaceEvidence):
            build_interaction_signals(race, entrants, AS_OF)

    def test_post_off_predictions_block(self):
        race, entrants = fixture()
        with self.assertRaises(InsufficientPreRaceEvidence):
            build_interaction_signals(race, entrants, "2026-10-28T08:00:01+00:00")

    def test_unverified_source_blocks(self):
        race, entrants = fixture()
        race["source"] = "web scrape"
        with self.assertRaises(InsufficientPreRaceEvidence):
            build_interaction_signals(race, entrants, AS_OF)

    def test_fake_physical_gate_numbers_not_permitted(self):
        race, entrants = fixture()
        race["physical_gate_assignment_verified"] = True
        for i, horse in enumerate(entrants):
            horse["physical_gate_slot"] = 1
        with self.assertRaises(InsufficientPreRaceEvidence):
            build_interaction_signals(race, entrants, AS_OF)

    def test_missing_timezone_blocks(self):
        race, entrants = fixture()
        with self.assertRaises(InsufficientPreRaceEvidence):
            build_interaction_signals(race, entrants, "2026-10-27T09:00:00")


if __name__ == "__main__":
    unittest.main()
