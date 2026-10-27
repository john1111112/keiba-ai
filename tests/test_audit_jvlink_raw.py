import tempfile
import unittest
from pathlib import Path

from src.jvlink_raw_store import store_records
from scripts.audit_jvlink_raw import audit_raw


class AuditRawTests(unittest.TestCase):
    def test_latest_observation_is_not_first_insert(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "raw.sqlite3"
            store_records(db, [b"RA12"], data_spec="0B15",
                          fetched_at="2026-10-26T12:00:00+09:00")
            store_records(db, [b"RA12"], data_spec="0B15",
                          fetched_at="2026-10-26T12:10:00+09:00")
            report = audit_raw(db)
            self.assertEqual(report["record_count"], 1)
            self.assertEqual(report["groups"][0]["observation_count"], 2)
            self.assertEqual(report["groups"][0]["last_received_at"],
                             "2026-10-26T12:10:00+09:00")
            self.assertEqual(report["bet_status"], "NO_BET")


if __name__ == "__main__":
    unittest.main()
