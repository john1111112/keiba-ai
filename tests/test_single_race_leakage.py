import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_prerace_auto_recorder import main


class SingleRaceLeakageTests(unittest.TestCase):
    def test_historical_parquet_is_rejected_without_reading_or_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "historical.parquet"
            path.write_bytes(b"do not open")
            with patch("scripts.run_prerace_auto_recorder.pd.read_parquet") as read, patch(
                    "scripts.run_prerace_auto_recorder.record_prerace_snapshot") as record:
                status = main(["--race-id", "r1",
                               "--race-time", "2026-10-26T15:40:00+09:00",
                               "--timing", "pre_30m",
                               "--features", str(path),
                               "--db", str(Path(temp) / "odds.db")])
            self.assertEqual(status, 2)
            read.assert_not_called()
            record.assert_not_called()
            self.assertFalse((Path(temp) / "odds.db").exists())

    def test_json_manifest_never_loaded_as_horse_features(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "upcoming.json"
            path.write_text('[{"race_id":"r1","race_status":"upcoming"}]')
            with patch("scripts.run_prerace_auto_recorder.record_prerace_snapshot",
                       return_value={"status": "NO_BET"}) as record:
                status = main(["--race-id", "r1",
                               "--race-time", "2026-10-26T15:40:00+09:00",
                               "--timing", "pre_30m",
                               "--features", str(path),
                               "--db", str(Path(temp) / "odds.db")])
            self.assertEqual(status, 0)
            self.assertIsNone(record.call_args.kwargs["features_df"])


if __name__ == "__main__":
    unittest.main()
