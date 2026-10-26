"""Pre-race inference must never consume known post-race result fields."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import pandas as pd

from src.prerace_recorder import record_prerace_snapshot, query_snapshot_records


class InferenceLeakageTests(unittest.TestCase):
    def test_result_column_blocks_model_without_calling_it(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "odds.db"
            model = Mock()
            features = pd.DataFrame([{
                "race_id": "r1", "horse_number": 1,
                "finish_order": 1, "pred_score": 0.9,
            }])
            result = record_prerace_snapshot(
                "r1", "2026-10-25T15:40:00+09:00", "pre_5m",
                features_df=features, win_model=model,
                odds_records=[],
                executed_at="2026-10-25T15:35:00+09:00", db_path=db)
            model.predict_win_race.assert_not_called()
            self.assertEqual(result["pred_status"], "blocked_uncertified_features")
            self.assertEqual(result["pred_count"], 0)
            audit = query_snapshot_records("r1", "pre_5m", db)
            self.assertEqual(audit["runs"][0]["pred_status"], "blocked_uncertified_features")
            self.assertEqual(audit["predictions"], [])

    def test_unrecognized_future_feature_also_blocks_model(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Mock()
            result = record_prerace_snapshot(
                "r3", "2026-10-25T15:40:00+09:00", "pre_5m",
                features_df=pd.DataFrame([{"race_id": "r3", "horse_number": 1,
                                           "mysterious_future_signal": 1}]),
                win_model=model, odds_records=[],
                executed_at="2026-10-25T15:35:00+09:00",
                db_path=Path(directory) / "odds.db")
            model.predict_win_race.assert_not_called()
            self.assertEqual(result["pred_status"], "blocked_uncertified_features")
            self.assertEqual(result["pred_count"], 0)

    def test_settled_odds_column_blocks_model(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Mock()
            result = record_prerace_snapshot(
                "r2", "2026-10-25T15:40:00+09:00", "pre_5m",
                features_df=pd.DataFrame([{"race_id": "r2", "horse_number": 1,
                                           "final_odds": 3.2}]),
                win_model=model, odds_records=[],
                executed_at="2026-10-25T15:35:00+09:00",
                db_path=Path(directory) / "odds.db")
            model.predict_win_race.assert_not_called()
            self.assertEqual(result["pred_status"], "blocked_uncertified_features")


if __name__ == "__main__":
    unittest.main()
