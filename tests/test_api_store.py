import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from api_server import ApiState, make_server, TOKEN
from maps_server import serve as maps_serve
from mapscout.api_store import ApiStore
from mapscout.scrape import ScrapeConfig
from mapscout.sheet import run_sheet

QUERIES = [
    {"key": "tyre shop|Sheffield", "query": "tyre shop in Sheffield",
     "locality": "Sheffield", "niche": "tyre shop"},
    {"key": "car garage|Sheffield", "query": "car garage in Sheffield",
     "locality": "Sheffield", "niche": "car garage"},
]


class TestApiStoreE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = ApiState()
        cls.api = make_server(cls.state)
        cls.api_port = cls.api.server_address[1]
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()
        cls.maps = maps_serve()
        cls.maps_port = cls.maps.server_address[1]
        threading.Thread(target=cls.maps.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown()
        cls.maps.shutdown()

    def test_sheet_run_through_api(self):
        cfg = ScrapeConfig(delay_seconds=0.05,
                           base_url=f"http://127.0.0.1:{self.maps_port}/maps")
        url = f"http://127.0.0.1:{self.api_port}/exec"
        store = ApiStore(url, TOKEN)
        s = run_sheet(store, QUERIES, cfg, log=lambda *a: None)
        self.assertEqual(s["queries_done"], 2)
        self.assertEqual(s["added"], 3)
        self.assertEqual(len(self.state.directory), 3)
        self.assertEqual(len(self.state.progress), 2)
        alpha = [r for r in self.state.directory if r[0] == "Alpha Tyres Ltd"][0]
        self.assertEqual(alpha[7], "0114 111 1111")          # phone column
        self.assertEqual(alpha[11], "0xaaa1:0xbbb1")          # place_key column
        # Fresh store (new run): state comes from the endpoint -> all done.
        store2 = ApiStore(url, TOKEN)
        s2 = run_sheet(store2, QUERIES, cfg, log=lambda *a: None)
        self.assertEqual(s2["queries_done"], 0)

    def test_bad_token_rejected(self):
        url = f"http://127.0.0.1:{self.api_port}/exec"
        store = ApiStore(url, "wrong")
        with self.assertRaises(RuntimeError):
            store.done_keys()


if __name__ == "__main__":
    unittest.main()


class TestFlakyEndpoint(unittest.TestCase):
    """Apps Script intermittently answers HTML instead of JSON (the
    production killer from the run logs). The client must retry and the
    data must still land exactly once."""

    @classmethod
    def setUpClass(cls):
        import mapscout.api_store as api_store
        api_store.RETRY_DELAYS = (0, 0.1, 0.2)
        cls.state = ApiState()
        cls.state.flaky_gets = 1
        cls.state.flaky_posts = 2
        cls.api = make_server(cls.state)
        cls.api_port = cls.api.server_address[1]
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()
        cls.maps = maps_serve()
        cls.maps_port = cls.maps.server_address[1]
        threading.Thread(target=cls.maps.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown()
        cls.maps.shutdown()

    def test_run_survives_flaky_endpoint(self):
        cfg = ScrapeConfig(delay_seconds=0.05,
                           base_url=f"http://127.0.0.1:{self.maps_port}/maps")
        store = ApiStore(f"http://127.0.0.1:{self.api_port}/exec", TOKEN)
        s = run_sheet(store, QUERIES, cfg, log=lambda *a: None)
        self.assertEqual(s["added"], 3)
        self.assertEqual(len(self.state.directory), 3)
        self.assertEqual(len(self.state.progress), 2)
