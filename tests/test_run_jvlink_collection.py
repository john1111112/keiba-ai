import sqlite3
import tempfile
import unittest
from pathlib import Path
from scripts.run_jvlink_collection import collect, main
from unittest.mock import patch


class Link:
    def JVInit(self, sid): return 0
    def JVOpen(self, spec, when, option): return 1
    def JVRead(self, size):
        if not hasattr(self, "called"):
            self.called = True
            return (4, b"RA12")
        return (0, b"")
    def JVClose(self): pass


class CollectionTests(unittest.TestCase):
    def test_collect_three_specs(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "jv.sqlite"
            report = collect(Link, db, sid="TEST", from_time="202610270000")
            self.assertEqual(report["status"], "RAW_CAPTURE_COMPLETE")
            self.assertEqual(len(report["reports"]), 3)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM jv_raw_records").fetchone()[0], 3)

    def test_empty_capture_not_marked_complete(self):
        class Empty(Link):
            def JVRead(self, size): return (0, b"")
        with tempfile.TemporaryDirectory() as folder:
            report = collect(Empty, Path(folder)/"db", sid="TEST", from_time="202610270000")
            self.assertEqual(report["status"], "RAW_CAPTURE_EMPTY")
            self.assertEqual(report["received"], 0)

    def test_invalid_date_rejected_before_com(self):
        with patch("scripts.run_jvlink_collection.open_jvlink") as sdk:
            with self.assertRaises(ValueError):
                main(["--sid", "TEST", "--from-time", "202613990000",
                      "--confirm-trial-active"])
            sdk.assert_not_called()

    def test_partial_failure(self):
        class Broken(Link):
            def JVInit(self, sid): return -1
        with tempfile.TemporaryDirectory() as folder:
            report = collect(Broken, Path(folder)/"db", sid="TEST", from_time="202610270000")
            self.assertEqual(report["status"], "RAW_CAPTURE_PARTIAL_FAILURE")
            self.assertTrue(all(r["status"] == "FAILED" for r in report["reports"]))


if __name__ == "__main__":
    unittest.main()
