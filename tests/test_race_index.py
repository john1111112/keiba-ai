import unittest

from scripts.render_race_index import render_index


class RaceIndexTests(unittest.TestCase):
    def test_escapes_untrusted_race_ids(self):
        html = render_index({"batch": {"files_found": 1, "inserted_rows": 1},
                             "dashboards": [{"race_id": "<script>alert(1)</script>",
                                             "html": "data/race_abc.html"}]})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('href="race_abc.html"', html)
        self.assertIn("NO BET", html)

    def test_rejects_nonstandard_report_names(self):
        html = render_index({"dashboards": [
            {"race_id": "bad", "html": "data/other.html"},
            {"race_id": "good", "html": "data/race_good.html"},
        ]})
        self.assertNotIn("other.html", html)
        self.assertIn("race_good.html", html)


if __name__ == "__main__":
    unittest.main()
