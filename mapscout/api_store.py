"""HTTP storage backend: talks to the Apps Script web app (Code.gs).

Same store interface as SheetStore (done_keys / known_place_keys /
append_directory / append_progress), so run_sheet works unchanged.
Directory rows are buffered and flushed together with the progress entry
for each query — one POST per query.

Apps Script answers POSTs with a 302 redirect to a one-time
googleusercontent URL; plain redirect following would downgrade the POST
to a GET and lose the body, so the redirect is followed manually.
"""

from __future__ import annotations

from dataclasses import asdict

from .scrape import Record
from .sheet import record_row


class ApiStore:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self._state: dict | None = None
        self._pending_directory: list[list[str]] = []

    def _fetch_state(self) -> dict:
        import requests  # lazy
        if self._state is None:
            r = requests.get(self.url, params={"token": self.token},
                             timeout=120)
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"directory API state error: {data}")
            self._state = data
        return self._state

    def done_keys(self) -> set[str]:
        return set(self._fetch_state().get("done_keys", []))

    def known_place_keys(self) -> set[str]:
        return set(self._fetch_state().get("place_keys", []))

    def append_directory(self, records: list[Record]):
        self._pending_directory.extend(record_row(r) for r in records)

    def append_progress(self, entry: dict):
        import requests  # lazy
        payload = {
            "token": self.token,
            "directory": self._pending_directory,
            "progress": [[entry["key"], entry["locality"], entry["niche"],
                          entry["query"], entry["found"], entry["added"],
                          entry["scraped_at"]]],
        }
        r = requests.post(self.url, json=payload, timeout=180,
                          allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            from urllib.parse import urljoin
            r = requests.post(urljoin(self.url, r.headers["Location"]),
                              json=payload, timeout=180)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"directory API append error: {data}")
        self._pending_directory = []
