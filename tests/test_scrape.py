import csv
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from maps_server import serve
from mapscout.scrape import ScrapeConfig, run_queries


class TestScrapeE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = serve()
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}/maps"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def cfg(self):
        return ScrapeConfig(delay_seconds=0.05, max_results=40,
                            base_url=self.base)

    def test_two_queries_dedupe_and_fields(self):
        with tempfile.TemporaryDirectory() as d:
            out, state = Path(d) / "dir.csv", Path(d) / "state.json"
            queries = [
                {"key": "tyre shop|Sheffield", "query": "tyre shop in Sheffield",
                 "locality": "Sheffield", "niche": "tyre shop"},
                {"key": "car garage|Sheffield", "query": "car garage in Sheffield",
                 "locality": "Sheffield", "niche": "car garage"},
            ]
            s = run_queries(queries, out, state, self.cfg(), log=lambda *a: None)
            self.assertEqual(s["queries_done"], 2)
            self.assertEqual(s["added"], 3)  # same 3 places in both queries
            rows = list(csv.DictReader(open(out)))
            self.assertEqual(len(rows), 3)
            alpha = [r for r in rows if r["name"] == "Alpha Tyres Ltd"][0]
            self.assertEqual(alpha["phone"], "0114 111 1111")
            self.assertEqual(alpha["website"], "https://alphatyres.example.co.uk")
            self.assertEqual(alpha["postcode"], "S1 2AB")
            self.assertEqual(alpha["town"], "Sheffield")
            self.assertEqual(alpha["place_key"], "0xaaa1:0xbbb1")
            self.assertEqual(alpha["rating"], "4.8")
            self.assertEqual(alpha["reviews"], "120")
            beta = [r for r in rows if r["name"] == "Beta Motors"][0]
            self.assertEqual(beta["phone"], "")

    def test_blocked_stops_then_resume(self):
        with tempfile.TemporaryDirectory() as d:
            out, state = Path(d) / "dir.csv", Path(d) / "state.json"
            blocked = [{"key": "x|Block", "query": "blockme in Sheffield",
                        "locality": "Block", "niche": "x"}]
            s = run_queries(blocked, out, state, self.cfg(), log=lambda *a: None)
            self.assertTrue(s["blocked"])
            self.assertEqual(s["queries_done"], 0)
            good = [{"key": "tyre shop|Sheffield", "query": "tyre shop in Sheffield",
                     "locality": "Sheffield", "niche": "tyre shop"}]
            s2 = run_queries(good, out, state, self.cfg(), log=lambda *a: None)
            self.assertEqual(s2["queries_done"], 1)
            s3 = run_queries(good, out, state, self.cfg(), log=lambda *a: None)
            self.assertEqual(s3["queries_done"], 0)  # already done -> skipped


if __name__ == "__main__":
    unittest.main()
