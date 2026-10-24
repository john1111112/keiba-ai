"""Tests for Issue Automation Safety Gate.

Verifies human allowlist validation, fail-closed access control,
concurrency file locking, and safe non-mutating dry runs.
"""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.issue_dispatch_gate import (
    evaluate_issue_dispatch,
    run_dry_run_inspection,
    FileLock
)

class IssueDispatchGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.allowlist_path = self.root / "allowlist.json"
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / ".test.lock"

        # Baseline allowlist
        self.allowlist_path.write_text(json.dumps({
            "repository": "john1111112/keiba-ai",
            "allowed_issues": {
                "11": {
                    "approved_by_user": False,
                    "action_mode": "dry_run",
                    "disallow_reasons": ["Manual approval required for Phase 13"]
                },
                "99": {
                    "approved_by_user": True,
                    "action_mode": "execute",
                    "disallow_reasons": []
                }
            }
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_unregistered_issue_disallowed(self):
        """未登録の Issue は拒絶 (DISALLOWED)"""
        res = evaluate_issue_dispatch(1234, allowlist_path=self.allowlist_path)
        self.assertFalse(res["approved"])
        self.assertEqual(res["decision"], "DISALLOWED")

    def test_registered_unapproved_issue_dry_run_only(self):
        """登録済みでも未承認の場合は DRY_RUN_ONLY"""
        res = evaluate_issue_dispatch(11, allowlist_path=self.allowlist_path)
        self.assertFalse(res["approved"])
        self.assertEqual(res["decision"], "DRY_RUN_ONLY")
        self.assertIn("Manual approval required", res["reason"])

    def test_registered_and_approved_issue(self):
        """明示的に承認された Issue は APPROVED"""
        res = evaluate_issue_dispatch(99, allowlist_path=self.allowlist_path)
        self.assertTrue(res["approved"])
        self.assertEqual(res["decision"], "APPROVED")
        self.assertEqual(res["action_mode"], "execute")

    def test_file_lock_prevents_concurrency(self):
        """二重ロックの競合防止"""
        with FileLock(self.lock_path):
            self.assertTrue(self.lock_path.exists())
            # Second attempt within active lock must fail
            with self.assertRaises(RuntimeError):
                with FileLock(self.lock_path):
                    pass
        # Once released, lock file is removed
        self.assertFalse(self.lock_path.exists())

    def test_dry_run_inspection_persists_state_without_mutation(self):
        """ドライラン実行で状態が記録され、コード変更は行われないこと"""
        res = run_dry_run_inspection(
            11,
            allowlist_path=self.allowlist_path,
            state_path=self.state_path
        )
        self.assertEqual(res["status"], "dry_run_completed")
        self.assertFalse(res["code_modified"])
        self.assertFalse(res["execution_occurred"])
        self.assertEqual(res["evaluation"]["decision"], "DRY_RUN_ONLY")

        # State file inspection
        self.assertTrue(self.state_path.exists())
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIn("11", state["issues"])
        self.assertTrue(state["issues"]["11"]["execution_skipped"])
        self.assertEqual(state["issues"]["11"]["last_decision"], "DRY_RUN_ONLY")

if __name__ == "__main__":
    unittest.main()
