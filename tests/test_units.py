import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mapscout.scrape import (fallback_key, place_key_from_url,
                             postcode_from_address, town_from_address, Record)
from mapscout.sheet import generate_queries, record_row
from mapscout.scrape import CSV_FIELDS


class TestKeys(unittest.TestCase):
    def test_feature_id(self):
        u = "https://www.google.com/maps/place/X/data=!4m2!3m1!1s0x48797852:0xABC123"
        self.assertEqual(place_key_from_url(u), "0x48797852:0xabc123")

    def test_no_feature_id(self):
        self.assertEqual(place_key_from_url("https://example.com"), "")

    def test_fallback(self):
        self.assertTrue(fallback_key("A B Tyres!", "s1 2ab", "0114 111").startswith("a b tyres|S1 2AB|"))


class TestAddressParsing(unittest.TestCase):
    def test_postcode(self):
        self.assertEqual(postcode_from_address("1 High St, Sheffield, S1 2AB"), "S1 2AB")
        self.assertEqual(postcode_from_address("Nowhere"), "")

    def test_town(self):
        self.assertEqual(town_from_address("1 High St, Sheffield, S1 2AB", "S1 2AB"), "Sheffield")
        self.assertEqual(town_from_address("Unit 5, Industrial Estate, Sheffield, S9 4EF", "S9 4EF"), "Sheffield")


class TestGenerateQueries(unittest.TestCase):
    def test_order_and_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "loc.csv"
            with open(p, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["name", "population"])
                w.writerow(["Smallville", 100])
                w.writerow(["Bigtown", 5000])
            qs = generate_queries(p, ["tyres", "garage"])
        self.assertEqual([q["key"] for q in qs],
                         ["tyres|Bigtown", "garage|Bigtown",
                          "tyres|Smallville", "garage|Smallville"])
        self.assertEqual(qs[0]["query"], "tyres in Bigtown")

    def test_record_row(self):
        row = record_row(Record(name="X", place_key="k"))
        self.assertEqual(len(row), len(CSV_FIELDS))
        self.assertEqual(row[CSV_FIELDS.index("name")], "X")


if __name__ == "__main__":
    unittest.main()
