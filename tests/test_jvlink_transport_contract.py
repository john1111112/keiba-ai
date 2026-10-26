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

    def test_jvgets_memoryview_preserves_raw_bytes(self):
        import base64
        class MemoryviewLink(FakeJVGetsLink):
            def JVGets(self, buffer, size, filename):
                if self.records:
                    payload = self.records.pop(0)
                    return len(payload), memoryview(payload), "JV_TESTFILE"
                return 0, (), ""

        raw_ra = race_rec()
        raw_se = horse_rec(1)
        link = MemoryviewLink([raw_ra, raw_se])
        result = execute_jvlink_fetch(link, "0B15", "20261027000000")
        self.assertEqual(result["fetch_status"], "SUCCESS")
        self.assertEqual(base64.b64decode(result["ra_records_b64"][0]), raw_ra)
        self.assertEqual(base64.b64decode(result["se_records_b64"][0]), raw_se)
        self.assertTrue(link.closed)

    def test_empty_published_day_is_not_a_transport_error(self):
        class NotPublished(FakeJVGetsLink):
            def JVRTOpen(self, data_spec, key):
                self.calls.append(("JVRTOpen", data_spec, key))
                return -1
        link = NotPublished([])
        result = execute_jvlink_fetch(
            link, "0B15", "20261027000000", mode="rt"
        )
        self.assertEqual(result["fetch_status"], "NO_DATA_AVAILABLE")
        self.assertEqual(result["records_count"], 0)
        self.assertEqual(result["ra_records"], [])

    def test_other_jvlink_open_error_is_still_failure(self):
        class BadAuth(FakeJVGetsLink):
            def JVRTOpen(self, data_spec, key):
                return -301
        result = execute_jvlink_fetch(
            BadAuth([]), "0B15", "20261027000000", mode="rt"
        )
        self.assertEqual(result["fetch_status"], "OPEN_FAILED_CODE_-301")

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

    def test_empty_sid_never_touches_com(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "worker.json"
            result = run_worker_capture(
                sid="", data_spec="0B15",
                from_time="20261027000000", output_path=str(out)
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["auth_status"], "SOFTWARE_ID_MISSING")
            self.assertTrue(out.is_file())

    def test_official_unknown_sid_can_initialize_in_development(self):
        import json
        import sys
        import tempfile
        import types
        from pathlib import Path
        from unittest.mock import patch

        fake = FakeJVGetsLink([race_rec(), horse_rec(1)])
        fake.JVInit = lambda sid: 0 if sid == "UNKNOWN" else -103
        progids = []
        win32com = types.ModuleType("win32com")
        win32com.__path__ = []
        client_module = types.ModuleType("win32com.client")
        def dispatch(progid):
            progids.append(progid)
            return fake
        client_module.Dispatch = dispatch
        win32com.client = client_module
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            sys.modules,
            {"win32com": win32com, "win32com.client": client_module}
        ), patch("src.jvlink_worker.check_is_32bit", return_value=True):
            output_path = Path(tmp) / "worker.json"
            result = run_worker_capture(
                sid="UNKNOWN", data_spec="0B15",
                from_time="20261027000000", output_path=str(output_path)
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["auth_status"], "AUTHENTICATED")
            self.assertEqual(result["fetch_status"], "SUCCESS")
            self.assertEqual(json.loads(output_path.read_text())["ra_count"], 1)
        self.assertEqual(progids, ["JVDTLab.JVLink"])

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
