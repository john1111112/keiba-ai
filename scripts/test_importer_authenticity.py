"""Importers must not invent a value when scraped observations are unavailable."""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class ImporterAuthenticityTests(unittest.TestCase):
    def test_bulk_parser_preserves_unknown_as_null(self):
        from scripts.fetch_bulk_real_jra import parse_netkeiba_html
        horses=[]
        for i in range(1,6):
            cells=[""]*20
            cells[0]=str(i)
            cells[1]="1"
            cells[2]=str(i)
            cells[3]=""
            cells[4]="牡3"
            cells[5]=""
            cells[6]=""
            cells[7]="1:35.0"
            cells[14]=""
            cells[15]=""
            cells[16]=""
            cells[17]=""
            cells[18]=""
            cells[19]=""
            horses.append("<tr>"+"".join("<td>"+v+"</td>" for v in cells)+"</tr>")
        html='<table class="race_table_01"><tr><th>header</th></tr>'+''.join(horses)+'</table>'
        rows=parse_netkeiba_html(html,"202501010101")
        self.assertEqual(5,len(rows))
        for r in rows:
            for j in [4,5,8,9,10,14,15,16,17,18,19,20,21,22,23,24,25]:
                self.assertIsNone(r[j],f"unexpected fabricated value for column {j}: {r[j]}")

    def test_no_hashed_pedigree_or_invented_market_price(self):
        for path in ["scripts/fetch_bulk_real_jra.py","scripts/populate_full_weekend.py"]:
            s=(ROOT/path).read_text(encoding="utf-8")
            self.assertNotIn("hashlib.md5",s)
            self.assertNotIn("p_pop * 2.8",s)
            self.assertNotIn('else "01000"',s)
            self.assertNotIn('else "01100"',s)
            self.assertNotIn('else 478.0',s)
            self.assertNotIn('else 15.0',s)

    def test_weekend_missing_father_or_style_is_unknown(self):
        from scripts.populate_full_weekend import get_sire_type, infer_running_style
        self.assertIsNone(get_sire_type(None))
        self.assertIsNone(get_sire_type("完全に不明な血統"))
        self.assertEqual(("先行",4),infer_running_style("3-4","horsename1"))
        self.assertEqual((None,None),infer_running_style("","horsename1"))

if __name__=="__main__":
    unittest.main()
