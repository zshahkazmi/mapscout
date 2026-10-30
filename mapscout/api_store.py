"""HTTP storage backend: talks to the Apps Script web app (Code.gs).

Same store interface as SheetStore (done_keys / known_place_keys /
append_directory / append_progress), so run_sheet works unchanged.
Directory rows are buffered and flushed together with the progress entry
for each query — one POST per query.

Apps Script mechanics (learned the hard way, from production logs):
the script executes on the FIRST request; the /exec URL then answers
with a 302 redirect to a one-time "echo" URL that serves the script's
JSON result to a GET. The echo URL rejects POSTs with HTTP 405 — so the
redirect must be followed the standard way (POST -> 302 -> GET), which
is exactly what requests does by default. Re-POSTing the body to the
echo URL was this project's costliest bug: the sheet write succeeded
server-side while the client died on the 405 page, killing every run
after exactly one query.

The Apps Script frontend can also intermittently answer with an HTML
error page, so every call is retried with backoff until a real JSON
reply arrives; failures report the HTTP status and a body snippet.
"""

from __future__ import annotations

import time

from .scrape import Record
from .sheet import record_row

RETRY_DELAYS = (0, 10, 30, 60)  # seconds before attempts 1..4


class ApiStore:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self._state: dict | None = None
        self._pending_directory: list[list[str]] = []

    # -- transport -------------------------------------------------------
    def _parse(self, response, what: str) -> dict:
        try:
            data = response.json()
        except ValueError:
            snippet = (response.text or "")[:200].replace("\n", " ")
            raise RuntimeError(
                f"{what}: non-JSON reply (HTTP {response.status_code}): "
                f"{snippet!r}")
        if not isinstance(data, dict) or "ok" not in data:
            raise RuntimeError(f"{what}: unexpected reply: {str(data)[:200]!r}")
        return data

    def _get_state_once(self) -> dict:
        import requests  # lazy
        r = requests.get(self.url, params={"token": self.token}, timeout=120)
        data = self._parse(r, "directory API state")
        if not data.get("ok"):
            raise RuntimeError(f"directory API state error: {data}")
        return data

    def _post_once(self, payload: dict) -> dict:
        import requests  # lazy
        # Default redirect handling is REQUIRED: Apps Script executes on
        # this POST, then 302-redirects to a one-time echo URL that
        # serves the JSON result to the follow-up GET.
        r = requests.post(self.url, json=payload, timeout=180)
        data = self._parse(r, "directory API append")
        if not data.get("ok"):
            raise RuntimeError(f"directory API append error: {data}")
        return data

    def _with_retries(self, fn, what: str):
        last_exc: Exception | None = None
        for attempt, delay in enumerate(RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                return fn()
            except Exception as e:  # transport errors + RuntimeError above
                last_exc = e
                if attempt < len(RETRY_DELAYS) - 1:
                    print(f"{what} failed ({type(e).__name__}: {e}); retrying...")
        raise RuntimeError(f"{what}: giving up after {len(RETRY_DELAYS)} "
                           f"attempts: {last_exc}")

    # -- store interface ---------------------------------------------------
    def _fetch_state(self) -> dict:
        if self._state is None:
            self._state = self._with_retries(self._get_state_once,
                                             "directory API state")
        return self._state

    def done_keys(self) -> set[str]:
        return set(self._fetch_state().get("done_keys", []))

    def known_place_keys(self) -> set[str]:
        return set(self._fetch_state().get("place_keys", []))

    def append_directory(self, records: list[Record]):
        self._pending_directory.extend(record_row(r) for r in records)

    def append_progress(self, entry: dict):
        payload = {
            "token": self.token,
            "directory": self._pending_directory,
            "progress": [[entry["key"], entry["locality"], entry["niche"],
                          entry["query"], entry["found"], entry["added"],
                          entry["scraped_at"]]],
        }
        self._with_retries(lambda: self._post_once(payload),
                           "directory API append")
        self._pending_directory = []
