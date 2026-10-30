"""A stub of the Apps Script endpoint (Code.gs) for tests.

Emulates the two Apps Script behaviours that matter: JSON replies with
{ok: ...}, and a 302 redirect on POST that the client must re-POST
through. State is kept in memory on the server object.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "test-token"


class ApiState:
    def __init__(self):
        self.directory = []   # rows (lists)
        self.progress = []    # rows (lists)


def make_server(state: ApiState) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if q.get("token", [""])[0] != TOKEN:
                return self._json({"ok": False, "error": "bad token"})
            self._json({
                "ok": True,
                "done_keys": [r[0] for r in state.progress],
                "place_keys": [r[11] for r in state.directory],
            })

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/exec":
                # Apps Script redirects POSTs; the client must re-POST.
                self.send_response(302)
                self.send_header("Location", "/usercontent")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/usercontent":
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body.get("token") != TOKEN:
                    return self._json({"ok": False, "error": "bad token"})
                state.directory.extend(body.get("directory", []))
                state.progress.extend(body.get("progress", []))
                return self._json({"ok": True,
                                   "directory_added": len(body.get("directory", [])),
                                   "progress_added": len(body.get("progress", []))})
            return self._json({"ok": False, "error": "no route"}, 404)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
