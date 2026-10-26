"""Offline JV-Link COM transport regression tests with no SDK or subscription."""
import unittest

from src.jvlink_worker import execute_jvlink_fetch, validate_records_integrity, run_worker_capture
from test_jvdata_spec_regression import race_rec, horse_rec, odds_rec


class FakeJVGetsLink:
    def __init__(self, records):
        self.records = list(records)
        self.calls = []
        self.closed = False

    def JVRTOpen(self, data_spec, key):
        self.calls.append(("JVRTOpen", data_spec, key))
        return 0

    def JVGets(self, buffer, size, filename):
        self.calls.append(("JVGets", size))
        if self.records:
            payload = self.records.pop(0)
            return len(payload), tuple(payload), "JV_TESTFILE"
        return 0, (), ""

    def JVClose(self):
        self.closed = True


class FakeJVOpenLink(FakeJVGetsLink):
    def JVOpen(self, data_spec, fromtime, option, readcount, downloadcount, lastfiletimestamp):
        self.calls.append(("JVOpen", data_spec, fromtime, option))
        return (0, len(self.records), 0, "")


class TransportContractTest(unittest.TestCase):
    def test_race_feed_uses_three_argument_jvgets_and_closes(self):
        link = FakeJVGetsLink([race_rec(), horse_rec(1)])
        result = execute_jvlink_fetch(link, "0B15", "20261027000000")
        self.assertEqual(result["fetch_status"], "SUCCESS")
        self.assertEqual(result["ra_count"], 1)
        self.assertEqual(result["se_count"], 1)
        self.assertTrue(link.closed)
        self.assertTrue(any(call[0] == "JVGets" for call in link.calls))

    def test_odds_only_batch_can_be_ingested(self):
        link = FakeJVGetsLink([odds_rec()])
        result = execute_jvlink_fetch(
            link, "0B31", "20261027000000",
            mode="rt", key="2026102705010101"
        )
        self.assertEqual(result["fetch_status"], "SUCCESS")
        self.assertEqual(result["o1_count"], 1)

    def test_stored_jvopen_uses_six_arguments(self):
        link = FakeJVOpenLink([race_rec(), horse_rec(1)])
        result = execute_jvlink_fetch(link, "RACE", "20261027000000", mode="stored")
        self.assertEqual(result["fetch_status"], "SUCCESS")
        self.assertEqual(link.calls[0], ("JVOpen", "RACE", "20261027000000", 1))

    def test_duplicate_header_updates_are_accepted(self):
        raw = race_rec().decode("cp932")
        horse = horse_rec(1).decode("cp932")
        self.assertTrue(validate_records_integrity([raw, raw], [horse], [])[0])

    def test_orphan_runner_rejected(self):
        raw = race_rec().decode("cp932")
        horse = horse_rec(1, "2026102706010102").decode("cp932")
        self.assertFalse(validate_records_integrity([raw], [horse], [])[0])

    def test_placeholder_sid_never_touches_com(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "worker.json"
            result = run_worker_capture(
                sid="UNKNOWN", data_spec="0B15",
                from_time="20261027000000", output_path=str(out)
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["auth_status"], "SOFTWARE_ID_MISSING")
            self.assertTrue(out.is_file())

    def test_truncated_jvgets_is_rejected(self):
        raw = race_rec()
        class Truncated(FakeJVGetsLink):
            def JVGets(self, buffer, size, filename):
                return len(raw), raw[:-2], "JV_TESTFILE"
        result = execute_jvlink_fetch(
            Truncated([raw]), "0B15", "20261027000000"
        )
        self.assertEqual(result["fetch_status"], "FETCH_ERROR")


if __name__ == "__main__":
    unittest.main()
