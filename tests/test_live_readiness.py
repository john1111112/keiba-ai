"""Regression tests for the read-only live readiness gate."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.check_live_readiness import main


class LiveReadinessTests(unittest.TestCase):
    def _run(self, report):
        output = io.StringIO()
        with patch("scripts.check_live_readiness.OddsProviderManager") as manager:
            manager.return_value.diagnose_all_providers.return_value = report
            with redirect_stdout(output):
                code = main()
        return code, json.loads(output.getvalue())

    def test_disconnected_sources_block_everything(self):
        code, result = self._run({"details": [{"provider": "JV-Link", "connected": False}]})
        self.assertEqual(code, 2)
        self.assertFalse(result["ready_for_live_odds"])
        self.assertFalse(result["ready_for_unattended_delivery"])
        self.assertFalse(result["ready_for_betting_recommendations"])
        self.assertTrue(result["blockers"])

    def test_provider_claim_does_not_certify_alerts_or_model(self):
        code, result = self._run({"details": [{"provider": "test", "connected": True}]})
        self.assertEqual(code, 2)
        self.assertFalse(result["ready_for_live_odds"])\n        self.assertTrue(result["provider_reports_connected"])
        self.assertFalse(result["ready_for_unattended_delivery"])
        self.assertFalse(result["ready_for_betting_recommendations"])

    def test_empty_diagnostics_fail_closed(self):
        code, result = self._run({})
        self.assertEqual(code, 2)
        self.assertFalse(result["ready_for_live_odds"])


if __name__ == "__main__":
    unittest.main()
