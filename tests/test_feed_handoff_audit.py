"""Tests for offline feed handoff metadata validation."""
import tempfile
import unittest
from pathlib import Path

from scripts.audit_feed_handoff import audit_handoff


def valid_payload():
    return {
        "provider_name": "Example Provider",
        "contact": "operator",
        "license_reference": "review-required",
        "data_format": "json",
        "timestamp_field": "published_at",
        "timezone": "Asia/Tokyo",
        "race_id_field": "race_id",
        "horse_id_field": "horse_number",
        "odds_field": "win_odds",
        "sample_path": "sample.json",
        "endpoint": "https://example.com/feed",
    }


class HandoffAuditTests(unittest.TestCase):
    def test_complete_metadata_is_not_certification(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "sample.json").write_text("{}", encoding="utf-8")
            result = audit_handoff(valid_payload(), root=temp)
            self.assertEqual(result["status"], "HANDOFF_FORMAT_COMPLETE")
            self.assertFalse(result["verified"])
            self.assertFalse(result["live_odds_enabled"])

    def test_missing_fields_fail_closed(self):
        result = audit_handoff({})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("MISSING_PROVIDER_NAME", result["issues"])

    def test_unsafe_sample_path_rejected(self):
        payload = valid_payload()
        payload["sample_path"] = "../private.json"
        self.assertIn("UNSAFE_SAMPLE_PATH", audit_handoff(payload)["issues"])

    def test_insecure_endpoint_rejected(self):
        payload = valid_payload()
        payload["endpoint"] = "http://example.com/feed"
        self.assertIn("INVALID_ENDPOINT", audit_handoff(payload)["issues"])

    def test_non_object_rejected(self):
        self.assertEqual(audit_handoff([])["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
