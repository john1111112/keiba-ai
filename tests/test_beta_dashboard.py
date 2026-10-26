import tempfile
import unittest
from pathlib import Path

from scripts.run_beta_dashboard import dashboard_html, _safe_reports, make_handler


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

    def test_handler_can_be_built_without_network(self):
        handler = make_handler(self.root)
        self.assertTrue(callable(handler))


if __name__ == "__main__":
    unittest.main()
