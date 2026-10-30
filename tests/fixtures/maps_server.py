"""A tiny fake Google Maps site for tests.

Serves a results feed and place panels with the same DOM hooks the engine
targets (role=feed, /maps/place/ links with feature ids, data-item-id
buttons). Any search returns the same three places; a search whose query
contains "blockme" returns an unusual-traffic page.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

PLACES = [
    {"slug": "alpha-tyres", "fid": "0xaaa1:0xbbb1", "name": "Alpha Tyres Ltd",
     "category": "Tyre shop", "rating": "4.8", "reviews": "120",
     "address": "1 High Street, Sheffield, S1 2AB",
     "phone": "0114 111 1111", "website": "https://alphatyres.example.co.uk",
     "hours": "Open · Closes 5:30 pm"},
    {"slug": "beta-motors", "fid": "0xaaa2:0xbbb2", "name": "Beta Motors",
     "category": "Car garage", "rating": "4.5", "reviews": "86",
     "address": "22 Low Road, Sheffield, S2 3CD",
     "phone": "", "website": "https://betamotors.example.co.uk",
     "hours": "Open · Closes 6 pm"},
    {"slug": "gamma-mot", "fid": "0xaaa3:0xbbb3", "name": "Gamma MOT Centre",
     "category": "MOT test centre", "rating": "4.9", "reviews": "210",
     "address": "Unit 5, Industrial Estate, Sheffield, S9 4EF",
     "phone": "0114 333 3333", "website": "",
     "hours": "Closed · Opens 8 am"},
]


def _search_html(origin: str) -> str:
    cards = []
    for p in PLACES:
        url = f"{origin}/maps/place/{p['slug']}/data=!4m2!3m1!1s{p['fid']}"
        cards.append(
            f'<div class="card"><a href="{url}" aria-label="{p["name"]}">'
            f'<div>{p["name"]}</div>'
            f'<div>{p["rating"]}({p["reviews"]})</div>'
            f'<div>{p["category"]} · {p["address"]}</div></a></div>')
    return ('<html><body><div role="feed">' + "".join(cards) +
            "</div></body></html>")


def _panel_html(p: dict) -> str:
    phone = (f'<button data-item-id="phone:tel:+44"><span>{p["phone"]}</span></button>'
             if p["phone"] else "")
    site = (f'<a data-item-id="authority" href="{p["website"]}">{p["website"]}</a>'
            if p["website"] else "")
    return f"""<html><body>
<h1>{p['name']}</h1>
<button jsaction="pane.rating.category">{p['category']}</button>
<div role="img" aria-label="{p['rating']} stars"></div>
<div role="img" aria-label="{p['reviews']} reviews"></div>
<button data-item-id="address"><span>{p['address']}</span></button>
{phone}{site}
<button data-item-id="hours" aria-label="{p['hours']}"></button>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        origin = f"http://{self.headers['Host']}"
        path = urlparse(self.path).path
        if path.startswith("/maps/search/"):
            q = unquote(path.rsplit("/", 1)[-1]).lower()
            if "blockme" in q:
                body = ("<html><body>Our systems have detected unusual "
                        "traffic from your computer network.</body></html>")
            else:
                body = _search_html(origin)
            return self._send(body)
        if path.startswith("/maps/place/"):
            slug = path.split("/")[3]
            for p in PLACES:
                if p["slug"] == slug:
                    return self._send(_panel_html(p))
            return self._send("<html><body>not found</body></html>", 404)
        return self._send("<html><body>maps fixture</body></html>")

    def _send(self, body: str, code: int = 200):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve() -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
