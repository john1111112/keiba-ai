"""Offline read-only dashboard regressions; synthetic feeds, no JRA access."""
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from scripts.build_live_jvlink_dashboard import build_live_dashboard


NOW = datetime(2026, 10, 27, 4, 26, tzinfo=timezone.utc)


def sample_race(odds_stamp="2026-10-27T13:25:00+09:00"):
    return {
        "race_id": "2026102705010101",
        "race_date": "2026-10-27",
        "race_time": "2026-10-27T15:00:00+09:00",
        "race_course": "東京",
        "surface": "芝",
        "distance": 1600.0,
        "race_status": "upcoming",
        "horses": [
            {"horse_number": 1, "horse_name": "一号", "win_odds": 3.2,
             "odds_observed_at": odds_stamp, "age": 5, "weight_carry": 56},
            {"horse_number": 2, "horse_name": "二号", "win_odds": 15.0,
             "odds_observed_at": odds_stamp, "age": 5, "weight_carry": 56},
        ],
    }


def make_feed(tmp):
    path = Path(tmp) / "latest_jvlink_feed.json"
    path.write_text(json.dumps({"races": [sample_race()]}), encoding="utf-8")
    return path


class LiveDashboardTest(unittest.TestCase):
    def test_no_feed_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_live_dashboard(Path(tmp) / "missing.json", now=NOW)
            self.assertEqual(report["status"], "NOT_READY")
            self.assertEqual(report["rows"], [])

    def test_unsigned_feed_must_not_run_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_feed(tmp)
            with patch("scripts.build_live_jvlink_dashboard.validate_feed_payload",
                       return_value=(True, "VALIDATED", [sample_race()], False)), \
                 patch("scripts.build_live_jvlink_dashboard.KeibaAIWinModel") as mdl:
                report = build_live_dashboard(path, now=NOW)
            self.assertEqual(report["status"], "UNVERIFIED_SOURCE")
            self.assertFalse(report["source_provenance_certified"])
            self.assertEqual(report["rows"], [])
            mdl.assert_not_called()

    def test_valid_feed_computes_fresh_ev_without_notifications(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_feed(tmp)
            model = MagicMock()
            model.predict_win_race.return_value = pd.DataFrame([
                {"horse_number": 1, "pred_win_prob": 0.35},
                {"horse_number": 2, "pred_win_prob": 0.65},
            ])
            with patch("scripts.build_live_jvlink_dashboard.validate_feed_payload",
                       return_value=(True, "VALIDATED", [sample_race()], True)), \
                 patch("scripts.build_live_jvlink_dashboard.KeibaAIWinModel",
                       return_value=model), \
                 patch("scripts.build_live_jvlink_dashboard.build_prerace_28features",
                       return_value=pd.DataFrame([{"horse_number": 1}, {"horse_number": 2}])):
                report = build_live_dashboard(path, now=NOW, max_odds_age_seconds=120)
            self.assertEqual(report["status"], "VERIFIED_LIVE_READ_ONLY")
            self.assertEqual(report["runners"], 2)
            self.assertEqual(report["rows"][0]["horse_number"], 2)
            self.assertEqual(report["rows"][0]["expected_value"], 9.75)
            self.assertEqual(report["rows"][1]["expected_value"], 1.12)
            self.assertEqual(report["bet_status"], "NO_BET")

    def test_stale_odds_do_not_generate_ev(self):
        race = sample_race("2026-10-27T13:00:00+09:00")
        with tempfile.TemporaryDirectory() as tmp:
            path = make_feed(tmp)
            model = MagicMock()
            model.predict_win_race.return_value = pd.DataFrame([
                {"horse_number": 1, "pred_win_prob": 0.4},
                {"horse_number": 2, "pred_win_prob": 0.6},
            ])
            with patch("scripts.build_live_jvlink_dashboard.validate_feed_payload",
                       return_value=(True, "VALIDATED", [race], True)), \
                 patch("scripts.build_live_jvlink_dashboard.KeibaAIWinModel",
                       return_value=model), \
                 patch("scripts.build_live_jvlink_dashboard.build_prerace_28features",
                       return_value=pd.DataFrame([{"horse_number": 1}, {"horse_number": 2}])):
                report = build_live_dashboard(path, now=NOW, max_odds_age_seconds=120)
            self.assertEqual(report["status"], "VERIFIED_LIVE_READ_ONLY")
            self.assertTrue(all(row["expected_value"] is None for row in report["rows"]))
            self.assertTrue(all(row["win_probability"] is not None for row in report["rows"]))
            self.assertTrue(all(row["odds_status"] == "STALE_OR_INVALID_ODDS"
                                for row in report["rows"]))

    def test_invalid_model_returns_no_fabricated_probabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_feed(tmp)
            with patch("scripts.build_live_jvlink_dashboard.validate_feed_payload",
                       return_value=(True, "VALIDATED", [sample_race()], True)), \
                 patch("scripts.build_live_jvlink_dashboard.KeibaAIWinModel",
                       side_effect=OSError("model not installed")):
                report = build_live_dashboard(path, now=NOW)
            self.assertEqual(report["status"], "MODEL_UNAVAILABLE")
            self.assertEqual(report["rows"], [])


if __name__ == "__main__":
    unittest.main()
