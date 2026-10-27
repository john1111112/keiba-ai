import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

class FullDatasetFailClosedTests(unittest.TestCase):
    def test_generator_cannot_create_any_races(self):
        from src.scraper import bulk_generate_large_historical_dataset, generate_sample_historical_data
        for f,kwargs in [(bulk_generate_large_historical_dataset,{"target_races":10}),
                         (generate_sample_historical_data,{"num_races":10})]:
            with self.assertRaisesRegex(RuntimeError,"SIMULATED_DATA_GENERATION_DISABLED"):
                f(**kwargs)

    def test_no_hard_coded_real_looking_date_when_unavailable(self):
        from src.scraper import fetch_netkeiba_race
        class FakeResp:
            status_code=200
            text='<div class="data_intro"><h1>11R Test Race</h1><p>芝 1600m</p><p class="smalltxt">no date recorded</p></div>'
            encoding=None
        with patch("src.scraper.requests.get", return_value=FakeResp()), patch("src.scraper.time.sleep",return_value=None):
            r,h,p=fetch_netkeiba_race("202501010101",delay_sec=0)
        self.assertIsNone(r)
        self.assertEqual([],h)
        self.assertEqual([],p)

    def test_legacy_fake_generation_removed_from_source(self):
        p=(ROOT/"src/scraper.py").read_text(encoding="utf-8")
        for text in ["random.shuffle(horse_order)", "random.gauss(0, 4.5)",
                     'f"競走馬_{i:04d}"', "winner_odds * 100"]:
            self.assertNotIn(text,p)

    def test_forensic_audit_does_not_mutate_database(self):
        p=(ROOT/"scripts/full_dataset_provenance_audit.py").read_text(encoding="utf-8")
        self.assertIn("?mode=ro",p)
        self.assertNotIn("UPDATE race_results",p)
        self.assertNotIn("DELETE FROM",p)

if __name__=="__main__":
    unittest.main()
