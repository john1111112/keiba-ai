from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.jvlink_raw_store import store_records


class JVRawStoreTests(unittest.TestCase):
    def test_binary_preserved_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "raw.sqlite3"
            raw = b"RA" + bytes([0x82, 0xa0, 0, 255])
            first = store_records(db, [raw], data_spec="0B15",
                                  fetched_at="2026-10-27T12:00:00+09:00")
            self.assertEqual(first["status"], "RAW_RECORDS_STORED_UNPARSED")
            store_records(db, [raw], data_spec="0B15",
                          fetched_at="2026-10-27T12:01:00+09:00")
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute(
                    "SELECT count(*) FROM jv_raw_records").fetchone()[0], 1)
                self.assertEqual(conn.execute(
                    "SELECT count(*) FROM jv_raw_observations").fetchone()[0], 2)
                self.assertEqual(conn.execute(
                    "SELECT payload FROM jv_raw_records").fetchone()[0], raw)

    def test_repeated_same_timestamp_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "raw.sqlite3"
            kwargs = {"data_spec": "0B15", "fetched_at": "2026-10-27T12:00:00+09:00"}
            first = store_records(db, [b"RAxx"], **kwargs)
            second = store_records(db, [b"RAxx"], **kwargs)
            self.assertEqual(first["observations_inserted"], 1)
            self.assertEqual(second["observations_inserted"], 0)

    def test_bad_spec_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                store_records(Path(folder) / "db", [b"RAxx"], data_spec="RACE")

    def test_naive_timestamp_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                store_records(Path(folder) / "db", [b"RAxx"],
                              data_spec="0B15", fetched_at="2026-10-27T12:00:00")


if __name__ == "__main__":
    unittest.main()
