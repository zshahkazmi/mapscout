"""A stub of the Apps Script endpoint (Code.gs) for tests.

Emulates the REAL Apps Script semantics that production taught us:
- The script executes on the first request (GET or POST to /exec).
- /exec then answers 302 to a one-time /echo URL that serves the stored
  JSON result to a GET — and rejects POSTs with HTTP 405 (an HTML page).
- The write itself dedupes by place_key / progress key, like Code.gs.
- Flaky modes make the final served response an HTML error page instead
  of JSON (the Apps Script frontend's intermittent failure).
"""

import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "test-token"


class ApiState:
    def __init__(self):
        self.directory = []   # rows (lists)
        self.progress = []    # rows (lists)
        self.flaky_gets = 0   # next N state GETs serve HTML instead of JSON
        self.flaky_posts = 0  # next N append POSTs serve HTML instead of JSON
        self.echo = {}        # one-time result store: key -> JSON bytes


def make_server(state: ApiState) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, data: bytes, code=200, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj).encode(), code)

        def _html_error(self):
            self._send(b"<html><body>Service error</body></html>",
                       200, "text/html")

        def _redirect_to_echo(self, obj):
            key = uuid.uuid4().hex
            state.echo[key] = json.dumps(obj).encode()
            self.send_response(302)
            self.send_header("Location", f"/echo?k={key}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # -- script execution (first request) -------------------------
        def _execute_get(self, query: dict):
            if query.get("token", [""])[0] != TOKEN:
                return {"ok": False, "error": "bad token"}
            return {"ok": True,
                    "done_keys": [r[0] for r in state.progress],
                    "place_keys": [r[11] for r in state.directory]}

        def _execute_post(self, body: dict):
            if body.get("token") != TOKEN:
                return {"ok": False, "error": "bad token"}
            seen = {r[11] for r in state.directory}
            added = 0
            for row in body.get("directory", []):
                if row[11] not in seen:
                    seen.add(row[11])
                    state.directory.append(row)
                    added += 1
            done = {r[0] for r in state.progress}
            p_added = 0
            for row in body.get("progress", []):
                if row[0] not in done:
                    done.add(row[0])
                    state.progress.append(row)
                    p_added += 1
            return {"ok": True, "directory_added": added,
                    "progress_added": p_added}

        # -- HTTP verbs -------------------------------------------------
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/echo":
                key = parse_qs(parsed.query).get("k", [""])[0]
                data = state.echo.pop(key, None)
                if data is None:
                    return self._json({"ok": False, "error": "echo expired"}, 404)
                return self._send(data)
            if parsed.path == "/exec":
                if state.flaky_gets > 0:
                    state.flaky_gets -= 1
                    return self._html_error()
                return self._redirect_to_echo(
                    self._execute_get(parse_qs(parsed.query)))
            return self._json({"ok": False, "error": "no route"}, 404)

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path == "/echo":
                # The production 405: echo URLs are GET-only.
                return self._send(b"<html><body>Method not allowed</body></html>",
                                  405, "text/html")
            if parsed.path == "/exec":
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                result = self._execute_post(body)
                if state.flaky_posts > 0:
                    state.flaky_posts -= 1
                    return self._html_error()
                return self._redirect_to_echo(result)
            return self._json({"ok": False, "error": "no route"}, 404)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
