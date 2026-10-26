"""Offline tests for 0B15 race and 0B31 per-race odds joining."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.jvlink_client import JVLinkClient, JVLinkBridgeError
from test_jvdata_spec_regression import race_rec, horse_rec, odds_rec, RACE


def worker_result(records, kind):
    return {
        "success": True,
        "auth_status": "AUTHENTICATED",
        "fetch_status": "SUCCESS",
        "records_count": len(records),
        "ra_records": [r.decode("cp932") for r in records] if kind == "RA" else [],
        "se_records": [r.decode("cp932") for r in records] if kind == "SE" else [],
        "o1_records": [r.decode("cp932") for r in records] if kind == "O1" else [],
    }


class OrchestrationTest(unittest.TestCase):
    def test_fetches_0b31_with_full_16_digit_race_key_and_joins_records(self):
        first = worker_result([race_rec()], "RA")
        first["se_records"] = [horse_rec(1).decode("cp932")]
        first["records_count"] = 2
        second = worker_result([odds_rec()], "O1")
        client = JVLinkClient()
        client.fetch_via_32bit_worker = MagicMock(side_effect=[first, second])
        client.process_records_to_feed = MagicMock(return_value={"races": []})
        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.jvlink_raw_store.store_records", return_value={"status": "OK"}
        ) as saved:
            client.fetch_and_build_live_feed(
                from_time="20261027000000", output_dir=Path(tmp)
            )
            self.assertEqual(client.fetch_via_32bit_worker.call_count, 2)
            self.assertEqual(
                client.fetch_via_32bit_worker.call_args_list[1].kwargs["key"], RACE
            )
            self.assertEqual(
                client.fetch_via_32bit_worker.call_args_list[1].kwargs["data_spec"], "0B31"
            )
            self.assertEqual(saved.call_count, 2)
            ingested = client.process_records_to_feed.call_args.args[0]
            self.assertEqual([r[:2] for r in ingested], [b"RA", b"SE", b"O1"])

    def test_missing_live_odds_do_not_create_synthetic_prices(self):
        first = worker_result([race_rec()], "RA")
        first["se_records"] = [horse_rec(1).decode("cp932")]
        first["records_count"] = 2
        client = JVLinkClient()
        client.fetch_via_32bit_worker = MagicMock(
            side_effect=[first, JVLinkBridgeError("no odds")]
        )
        client.process_records_to_feed = MagicMock(return_value={"races": []})
        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.jvlink_raw_store.store_records", return_value={"status": "OK"}
        ) as saved:
            client.fetch_and_build_live_feed(
                from_time="20261027000000", output_dir=Path(tmp)
            )
            ingested = client.process_records_to_feed.call_args.args[0]
            self.assertEqual([r[:2] for r in ingested], [b"RA", b"SE"])
            self.assertEqual(saved.call_count, 1)
            diagnostics = json.loads(
                (Path(tmp) / "latest_odds_fetch_errors.json").read_text(encoding="utf-8")
            )
            self.assertIn(RACE, diagnostics)
            self.assertIn("no odds", diagnostics[RACE])


if __name__ == "__main__":
    unittest.main()
