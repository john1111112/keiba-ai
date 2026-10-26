from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from src.jvlink_ingest import capture, JVLinkError


class FakeLink:
    def __init__(self, replies=None, open_code=0):
        self.replies = iter(replies or [(4, b"RA12"), (0, b"")])
        self.open_code = open_code
        self.closed = 0
    def JVInit(self, sid):
        return 0
    def JVOpen(self, spec, start, option):
        return self.open_code
    def JVRead(self, size):
        return next(self.replies)
    def JVClose(self):
        self.closed += 1


class JVLinkIngestTests(unittest.TestCase):
    def test_reads_stores_and_closes(self):
        with tempfile.TemporaryDirectory() as folder:
            link = FakeLink()
            db = Path(folder) / "jv.sqlite"
            result = capture(link, db, sid="TEST", data_spec="0B15",
                             from_time="202610270000")
            self.assertEqual(result["received"], 1)
            self.assertEqual(link.closed, 1)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT payload FROM jv_raw_records").fetchone()[0], b"RA12")

    def test_limit_allows_final_eof(self):
        with tempfile.TemporaryDirectory() as folder:
            result = capture(FakeLink(), Path(folder)/"db", sid="TEST",
                             data_spec="0B15", from_time="202610270000",
                             max_records=1)
            self.assertEqual(result["received"], 1)

    def test_limit_rejects_second_record(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder)/"db"
            link = FakeLink(replies=[(4, b"RA12"), (4, b"RA13"), (0, b"")])
            with self.assertRaisesRegex(JVLinkError, "record limit"):
                capture(link, db, sid="TEST", data_spec="0B15",
                        from_time="202610270000", max_records=1)
            self.assertFalse(db.exists())

    def test_positive_jvopen_record_count(self):
        with tempfile.TemporaryDirectory() as folder:
            link = FakeLink(open_code=2)
            result = capture(link, Path(folder)/"db", sid="TEST",
                             data_spec="0B15", from_time="202610270000")
            self.assertEqual(result["received"], 1)
            self.assertEqual(link.closed, 1)

    def test_async_wait_then_record(self):
        with tempfile.TemporaryDirectory() as folder:
            link = FakeLink(replies=[(-3, b""), (4, b"RA12"), (0, b"")])
            result = capture(link, Path(folder)/"db", sid="TEST",
                             data_spec="0B30", from_time="202610270000",
                             sleep=lambda _: None)
            self.assertEqual(result["received"], 1)
            self.assertEqual(link.closed, 1)

    def test_byte_budget_rejects_partial_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "db"
            link = FakeLink(replies=[(4097, b"RA" + b"x" * 4095), (0, b"")])
            with self.assertRaisesRegex(JVLinkError, "byte budget"):
                capture(link, db, sid="TEST", data_spec="0B15",
                        from_time="202610270000", max_total_bytes=4096)
            self.assertFalse(db.exists())
            self.assertEqual(link.closed, 1)

    def test_error_closes(self):
        with tempfile.TemporaryDirectory() as folder:
            link = FakeLink(open_code=-1)
            with self.assertRaises(JVLinkError):
                capture(link, Path(folder)/"db", sid="TEST",
                        data_spec="0B30", from_time="202610270000")
            self.assertEqual(link.closed, 0)

    def test_invalid_calendar_date_rejected(self):
        with self.assertRaises(ValueError):
            capture(FakeLink(), "db", sid="TEST",
                    data_spec="0B41", from_time="202613990000")

    def test_text_buffers_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            link = FakeLink(replies=[(4, "RA12"), (0, b"")])
            with self.assertRaises(JVLinkError):
                capture(link, Path(folder)/"db", sid="TEST",
                        data_spec="0B41", from_time="202610270000")
            self.assertEqual(link.closed, 1)

    def test_invalid_timestamp_does_not_call_sdk(self):
        with self.assertRaises(ValueError):
            capture(FakeLink(), "db", sid="TEST",
                    data_spec="0B41", from_time="yesterday")


if __name__ == "__main__":
    unittest.main()
