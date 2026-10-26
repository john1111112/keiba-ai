import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.run_beta_dashboard import dashboard_html, _safe_reports, _upcoming_rows, _manual_odds_rows, _background_status, make_handler


class BetaDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_reports_still_usable(self):
        page = dashboard_html(self.root)
        self.assertIn("NO BET", page)
        self.assertIn("まだレース分析レポートがありません", page)
        self.assertIn('content="60"', page)

    def test_safe_links_only(self):
        good = "race_" + "a" * 20 + ".html"
        (self.root / good).write_text("<h1>race</h1>", encoding="utf-8")
        (self.root / "race_bad.html").write_text("bad", encoding="utf-8")
        (self.root / ("race_" + "b" * 20 + "_timeline.json")).write_text("{}")
        self.assertEqual(_safe_reports(self.root), [good])
        page = dashboard_html(self.root)
        self.assertIn("/reports/" + good, page)
        self.assertNotIn("race_bad", page)

    def test_upcoming_manifest_excludes_past_and_unverified_status(self):
        manifest = self.root / "upcoming.json"
        manifest.write_text(json.dumps([
            {"race_id": "future<race>", "race_status": "upcoming",
             "race_time": "2026-10-27T15:00:00+09:00"},
            {"race_id": "past", "race_status": "upcoming",
             "race_time": "2026-10-25T15:00:00+09:00"},
            {"race_id": "result", "race_status": "finished",
             "race_time": "2026-10-27T15:00:00+09:00"}
        ]), encoding="utf-8")
        from datetime import datetime, timezone
        now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        rows = _upcoming_rows(manifest, now=now)
        self.assertEqual([race for race, _ in rows], ["future<race>"])
        page = dashboard_html(self.root, manifest_path=manifest, now=now)
        self.assertIn("future&lt;race&gt;", page)
        self.assertNotIn("future<race>", page)
        self.assertNotIn("<td>past</td>", page)
        self.assertIn("NO BET", page)

    def test_manifest_conflicting_start_fails_closed(self):
        manifest = self.root / "upcoming.json"
        manifest.write_text(json.dumps([
            {"race_id": "r1", "race_status": "upcoming",
             "race_time": "2099-01-01T15:00:00+09:00"},
            {"race_id": "r1", "race_status": "upcoming",
             "race_time": "2099-01-01T15:10:00+09:00"}
        ]), encoding="utf-8")
        self.assertEqual(_upcoming_rows(manifest), [])

    def test_odds_table_excludes_final_future_and_wrong_race(self):
        from datetime import datetime, timezone
        db = self.root / "odds.db"
        with sqlite3.connect(db) as conn:
            conn.execute("""CREATE TABLE odds_timeline (
                race_id TEXT, horse_number INTEGER, odds_value REAL,
                observed_at TEXT, fetched_at TEXT, source TEXT,
                data_status TEXT, odds_type TEXT)""")
            base = ("r1", 3, 4.2, "2026-10-26T09:00:00+09:00",
                    "2026-10-26T09:01:00+09:00", "manual", "manual_prerace", "win")
            conn.execute("INSERT INTO odds_timeline VALUES (?,?,?,?,?,?,?,?)", base)
            conn.execute("INSERT INTO odds_timeline VALUES (?,?,?,?,?,?,?,?)",
                         ("r1", 4, 8.0, *base[3:6], "final_settled", "win"))
            conn.execute("INSERT INTO odds_timeline VALUES (?,?,?,?,?,?,?,?)",
                         ("r1", 5, 9.0, "2026-10-26T12:00:00+09:00",
                          "2026-10-26T12:01:00+09:00", "manual", "manual_prerace", "win"))
            conn.execute("INSERT INTO odds_timeline VALUES (?,?,?,?,?,?,?,?)",
                         ("r2", 6, 7.0, *base[3:]))
        now = datetime.fromisoformat("2026-10-26T09:05:00+09:00")
        upcoming = [("r1", datetime.fromisoformat("2026-10-26T15:00:00+09:00"))]
        rows = _manual_odds_rows(db, upcoming, now=now)
        self.assertEqual([(r[0], r[1]) for r in rows], [("r1", 3)])
        manifest = self.root / "upcoming.json"
        manifest.write_text(json.dumps([{"race_id": "r1", "race_status": "upcoming",
                                         "race_time": "2026-10-26T15:00:00+09:00"}]))
        page = dashboard_html(self.root, manifest_path=manifest, odds_db=db, now=now)
        self.assertIn("4.20", page)
        self.assertIn("AI勝率", page)
        self.assertIn("NO BET", page)
        self.assertNotIn("9.00", page)

    def test_stale_odds_never_displayed(self):
        from datetime import datetime
        db = self.root / "odds.db"
        with sqlite3.connect(db) as conn:
            conn.execute("""CREATE TABLE odds_timeline (
                race_id TEXT, horse_number INTEGER, odds_value REAL,
                observed_at TEXT, fetched_at TEXT, source TEXT,
                data_status TEXT, odds_type TEXT)""")
            conn.execute("INSERT INTO odds_timeline VALUES (?,?,?,?,?,?,?,?)",
                         ("r1", 3, 4.2, "2026-10-26T09:00:00+09:00",
                          "2026-10-26T09:01:00+09:00", "manual",
                          "manual_prerace", "win"))
        upcoming = [("r1", datetime.fromisoformat("2026-10-26T15:00:00+09:00"))]
        self.assertEqual(_manual_odds_rows(
            db, upcoming, now=datetime.fromisoformat("2026-10-26T09:12:00+09:00")), [])
        self.assertEqual(len(_manual_odds_rows(
            db, upcoming, now=datetime.fromisoformat("2026-10-26T09:05:00+09:00"))), 1)

    def test_background_status_fail_closed_and_escaped(self):
        status = self.root / "scan.json"
        self.assertIn("未実行", _background_status(status))
        status.write_text(json.dumps({"status": "<script>alert(1)</script>",
                                      "bet_status": "BET"}), encoding="utf-8")
        self.assertEqual(_background_status(status), "結果ファイル不正")
        status.write_text(json.dumps({"status": "SCAN_BLOCKED",
                                      "bet_status": "BET"}), encoding="utf-8")
        page = dashboard_html(self.root, scan_status=status)
        self.assertIn("SCAN_BLOCKED", page)
        self.assertIn("NO BET", page)
        self.assertIn("未認証", page)
        self.assertNotIn("<script>", page)

    def test_handler_can_be_built_without_network(self):
        handler = make_handler(self.root)
        self.assertTrue(callable(handler))


if __name__ == "__main__":
    unittest.main()
