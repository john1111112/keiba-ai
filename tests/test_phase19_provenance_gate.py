import unittest
from scripts.phase19_provenance_gate import assess_phase19_provenance


class Phase19ProvenanceGateTests(unittest.TestCase):
    def test_known_fabricated_times_blocked(self):
        source = 'race_start_at = f"{r_date}T10:00:00Z"\n'
        report = assess_phase19_provenance(source)
        self.assertEqual(report["status"], "BLOCKED_UNVERIFIED_RACE_TIME")
        self.assertFalse(report["final_holdout_eligible"])
        self.assertIsNone(report["roi"])

    def test_removing_literal_does_not_automatically_certify(self):
        report = assess_phase19_provenance("race_start_at = record['start_time']")
        self.assertEqual(report["status"], "NOT_EVALUABLE_PROVENANCE_NOT_ATTESTED")
        self.assertFalse(report["final_holdout_eligible"])


if __name__ == "__main__":
    unittest.main()
