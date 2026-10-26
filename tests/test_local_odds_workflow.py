import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.run_local_odds_workflow import run_local_workflow


def snapshot(race, start="2026-10-26T15:40:00+09:00", fetched="2026-10-26T15:30:05+09:00"):
    return {"race_id": race, "race_start_time": start,
            "observed_at": "2026-10-26T15:30:00+09:00",
            "fetched_at": fetched, "source": "manual_screen",
            "odds": [{"horse_number": 1, "odds_value": 4.5}]}


class LocalWorkflowTests(unittest.TestCase):
    def test_full_workflow_and_idempotent_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            (folder / "one.json").write_text(json.dumps(snapshot("race-1")), encoding="utf-8")
            (folder / "two.json").write_text(json.dumps(snapshot("race-2")), encoding="utf-8")
            db, out = root / "odds.db", root / "reports"
            result = run_local_workflow(folder, db, out)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["batch"]["inserted_rows"], 2)
            self.assertEqual(len(result["dashboards"]), 2)
            for item in result["dashboards"]:
                timeline = json.loads(Path(item["timeline"]).read_text(encoding="utf-8"))
                self.assertEqual(timeline["bet_status"], "NO_BET")
                self.assertEqual(len(timeline["observations"]), 1)
                self.assertIn("NO BET", Path(item["market"]).read_text(encoding="utf-8"))
            self.assertIn("NO BET", (out / "index.html").read_text(encoding="utf-8"))
            self.assertEqual(Path(result["index"]), out / "index.html")
            self.assertEqual(json.loads((out / "quality_audit.json").read_text())["eligible_rows"], 2)
            for item in result["dashboards"]:
                self.assertIn("NO BET", Path(item["html"]).read_text(encoding="utf-8"))
            again = run_local_workflow(folder, db, out)
            self.assertEqual(again["batch"]["inserted_rows"], 0)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM odds_timeline").fetchone()[0], 2)

    def test_two_snapshots_generate_movement_report_and_index_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            early = snapshot("race-1")
            early["observed_at"] = "2026-10-26T15:19:00+09:00"
            early["fetched_at"] = "2026-10-26T15:19:05+09:00"
            early["odds"][0]["odds_value"] = 5.0
            later = snapshot("race-1")
            later["observed_at"] = "2026-10-26T15:29:00+09:00"
            later["fetched_at"] = "2026-10-26T15:29:05+09:00"
            later["odds"][0]["odds_value"] = 4.0
            (folder / "early.json").write_text(json.dumps(early), encoding="utf-8")
            (folder / "later.json").write_text(json.dumps(later), encoding="utf-8")
            result = run_local_workflow(folder, root / "odds.db", root / "reports")
            self.assertEqual(result["status"], "COMPLETE")
            movement = result["dashboards"][0]["movement"]
            self.assertIsNotNone(movement)
            report = json.loads(Path(movement).read_text(encoding="utf-8"))
            self.assertEqual(report["changes"][0]["change_percent"], -20.0)
            self.assertEqual(report["bet_status"], "NO_BET")
            self.assertIn("オッズ変動", (root / "reports" / "index.html").read_text(encoding="utf-8"))
            self.assertIn("オッズ一覧", (root / "reports" / "index.html").read_text(encoding="utf-8"))

    def test_late_arrival_does_not_create_false_movement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            early = snapshot("race-late")
            early["observed_at"] = "2026-10-26T15:19:00+09:00"
            early["fetched_at"] = "2026-10-26T15:31:00+09:00"
            early["odds"][0]["odds_value"] = 5.0
            later = snapshot("race-late")
            later["observed_at"] = "2026-10-26T15:29:00+09:00"
            later["fetched_at"] = "2026-10-26T15:29:05+09:00"
            later["odds"][0]["odds_value"] = 4.0
            (folder / "early.json").write_text(json.dumps(early), encoding="utf-8")
            (folder / "later.json").write_text(json.dumps(later), encoding="utf-8")
            result = run_local_workflow(folder, root / "odds.db", root / "reports")
            self.assertEqual(result["status"], "COMPLETE")
            movement = result["dashboards"][0]["movement"]
            self.assertIsNotNone(movement)
            report = json.loads(Path(movement).read_text(encoding="utf-8"))
            self.assertEqual(report["changes"], [])

    def test_dry_run_no_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            (folder / "one.json").write_text(json.dumps(snapshot("race-1")), encoding="utf-8")
            db, out = root / "odds.db", root / "reports"
            result = run_local_workflow(folder, db, out, dry_run=True)
            self.assertEqual(result["status"], "VALIDATED_ONLY")
            self.assertFalse(db.exists())
            self.assertFalse(out.exists())

    def test_conflicting_race_starts_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            (folder / "a.json").write_text(json.dumps(snapshot("race-1")), encoding="utf-8")
            (folder / "b.json").write_text(json.dumps(snapshot(
                "race-1", start="2026-10-26T15:45:00+09:00")), encoding="utf-8")
            db = root / "odds.db"
            with self.assertRaises(ValueError):
                run_local_workflow(folder, db, root / "reports")
            self.assertFalse(db.exists())

    def test_bad_file_reported_but_good_race_dashboard_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            (folder / "bad.json").write_text("{", encoding="utf-8")
            (folder / "good.json").write_text(json.dumps(snapshot("r1")), encoding="utf-8")
            result = run_local_workflow(folder, root / "odds.db", root / "reports")
            self.assertEqual(result["status"], "PARTIAL_FAILURE")
            self.assertEqual(result["batch"]["rejected_files"], 1)
            self.assertEqual(len(result["dashboards"]), 1)

    def test_race_id_cannot_escape_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots"
            folder.mkdir()
            (folder / "one.json").write_text(json.dumps(snapshot("../outside")), encoding="utf-8")
            out = root / "reports"
            result = run_local_workflow(folder, root / "odds.db", out)
            self.assertEqual(len(result["dashboards"]), 1)
            self.assertEqual(Path(result["dashboards"][0]["html"]).parent, out)
            self.assertFalse((root / "outside.html").exists())


if __name__ == "__main__":
    unittest.main()
