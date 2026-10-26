"""Regression tests using synthetic fixed-width records from JRA-VAN JV-Data 4.9.0.1.

These fixtures are deliberately synthetic, never claimed to be live JRA data.
Sections: RA p.8, SE p.9, O1 p.12 (PDF page indices 10, 11, 14).
"""
import unittest

from src.jvlink_adapter import (
    parse_jv_ra_record, parse_jv_se_record, parse_jv_o1_record,
    convert_multiple_jvlink_records_to_feed,
)

RACE = "2026102705010101"


def rec(kind, size, race=RACE, kubun="2", created="20261027"):
    record = bytearray(b" " * (size - 2) + b"\r\n")
    record[:2] = kind.encode("ascii")
    record[2:3] = kubun.encode("ascii")
    record[3:11] = created.encode("ascii")
    record[11:27] = race.encode("ascii")
    return record


def race_rec(race=RACE):
    raw = rec("RA", 1272, race)
    raw[697:701] = b"1600"  # distance at 698, 1-indexed
    raw[705:707] = b"11"    # turf left at 706
    raw[873:877] = b"1500"  # post time at 874
    raw[881:883] = b"02"    # registered runners at 882
    raw[888:890] = b"00"    # ground is unknown pre-race
    return bytes(raw)


def horse_rec(num, race=RACE, name="サンプル"):
    raw = rec("SE", 555, race)
    raw[27:28] = b"1"
    raw[28:30] = f"{num:02d}".encode("ascii")
    raw[30:40] = f"20211000{num:02d}".encode("ascii")
    raw[40:76] = name.encode("cp932").ljust(36, b" ")
    raw[78:79] = b"1"     # sex at 79
    raw[82:84] = b"05"    # age at 83
    raw[288:291] = b"560" # carried weight at 289
    raw[296:301] = b"01234"  # jockey at 297
    raw[324:327] = b"480" # current weight at 325
    raw[328:331] = b"+02"
    return bytes(raw)


def odds_rec(race=RACE, kubun="1", created="20261027", announce="10271325"):
    raw = rec("O1", 962, race, kubun, created)
    raw[27:35] = announce.encode("ascii")
    raw[35:37] = b"02"  # registered count
    raw[37:39] = b"02"  # started count
    raw[39:40] = b"7"   # win-bet sale available
    raw[40:42] = b"7"
    raw[42:43] = b"3"
    raw[43:51] = b"01003201"  # horse 1, 3.2x, rank 01
    raw[51:59] = b"02015002"  # horse 2, 15x
    return bytes(raw)


class OfficialByteLayoutTest(unittest.TestCase):
    def test_ra_header_offsets(self):
        result = parse_jv_ra_record(race_rec())
        self.assertEqual(result["race_key"], RACE)
        self.assertEqual(result["race_time"], "2026-10-27T15:00:00+09:00")
        self.assertEqual(result["distance"], 1600.0)
        self.assertEqual(result["surface"], "芝")
        self.assertEqual(result["direction"], "左")
        self.assertIsNone(result["condition"])

    def test_se_offsets_and_multibyte_name(self):
        result = parse_jv_se_record(horse_rec(2))
        self.assertEqual(result["horse_number"], 2)
        self.assertEqual(result["horse_name"], "サンプル")
        self.assertEqual(result["jockey_id"], "01234")
        self.assertEqual(result["age"], 5.0)
        self.assertEqual(result["weight_carry"], 56.0)

    def test_o1_win_odds_and_announcement_time(self):
        result = parse_jv_o1_record(odds_rec())
        self.assertEqual(result["race_key"], RACE)
        self.assertEqual(result["odds_map"][1], (3.2, "2026-10-27T13:25:00+09:00"))
        self.assertEqual(result["odds_map"][2][0], 15.0)

    def test_o1_year_boundary(self):
        race = "2027010105010101"
        result = parse_jv_o1_record(odds_rec(race, created="20261231", announce="12312350"))
        self.assertEqual(result["odds_map"][1][1], "2026-12-31T23:50:00+09:00")

    def test_o1_non_sale_flags_return_no_odds(self):
        raw = bytearray(odds_rec())
        raw[39:40] = b"0"
        self.assertEqual(parse_jv_o1_record(bytes(raw))["odds_map"], {})

    def test_o1_capped_odds_not_treated_as_actual_quote(self):
        raw = bytearray(odds_rec())
        raw[45:49] = b"9999"
        result = parse_jv_o1_record(bytes(raw))
        self.assertNotIn(1, result["odds_map"])
        self.assertIn(2, result["odds_map"])

    def test_o1_cancelled_odds_not_treated_as_price(self):
        raw = bytearray(odds_rec())
        raw[45:49] = b"----"
        self.assertNotIn(1, parse_jv_o1_record(bytes(raw))["odds_map"])

    def test_o1_wrong_size_fails(self):
        with self.assertRaises(ValueError):
            parse_jv_o1_record(odds_rec()[:500])

    def test_o1_unverified_or_final_data_not_eligible_for_prerace(self):
        with self.assertRaises(ValueError):
            parse_jv_o1_record(odds_rec(kubun="4"))

    def test_multirace_odd_mapping_separated(self):
        other = "2026102706010102"
        raw = [
            horse_rec(1, other), race_rec(), horse_rec(1), odds_rec(),
            race_rec(other), odds_rec(other),
        ]
        feed = convert_multiple_jvlink_records_to_feed(raw)
        self.assertEqual(len(feed["races"]), 2)
        self.assertEqual(
            {race["race_id"] for race in feed["races"]},
            {RACE, other},
        )
        self.assertTrue(all(race["horses"][0]["win_odds"] == 3.2 for race in feed["races"]))


if __name__ == "__main__":
    unittest.main()
