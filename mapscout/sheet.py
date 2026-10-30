"""Google Sheets storage for mapscout (service-account access via gspread).

The workbook has two tabs:
- "Directory": one row per unique business (same columns as the CSV).
- "Progress": one row per completed query — this is the resumable state.
  A query is identified by its key "niche|locality".

gspread is imported lazily so the core scraper needs no Google dependency.
"""

from __future__ import annotations

import csv
import time
from dataclasses import asdict
from pathlib import Path

from .scrape import (CSV_FIELDS, BlockedError, MapsScraper, Record,
                      ScrapeConfig)

PROGRESS_HEADERS = ["key", "locality", "niche", "query", "found", "added",
                    "scraped_at"]


def record_row(rec: Record) -> list[str]:
    d = asdict(rec)
    return [str(d.get(f, "") or "") for f in CSV_FIELDS]


def generate_queries(localities_csv: Path, niches: list[str],
                     min_population: int = 0,
                     limit_localities: int = 0) -> list[dict]:
    """Locality-major query matrix (biggest places first)."""
    rows = []
    with open(localities_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pop = int(r.get("population") or 0)
            if pop < min_population:
                continue
            rows.append(r)
    rows.sort(key=lambda r: -int(r.get("population") or 0))
    if limit_localities:
        rows = rows[:limit_localities]
    queries = []
    for r in rows:
        name = r["name"].strip()
        for niche in niches:
            queries.append({
                "key": f"{niche}|{name}",
                "query": f"{niche} in {name}",
                "locality": name,
                "niche": niche,
            })
    return queries


def load_niches(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


class SheetStore:
    def __init__(self, sa_key_path: str, sheet_id: str):
        import gspread  # lazy: only needed for sheet runs
        gc = gspread.service_account(filename=sa_key_path)
        self.book = gc.open_by_key(sheet_id)
        self.directory = self._tab("Directory", CSV_FIELDS)
        self.progress = self._tab("Progress", PROGRESS_HEADERS)

    def _tab(self, title: str, headers: list[str]):
        import gspread
        try:
            ws = self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(title=title, rows=1000, cols=len(headers))
        if ws.row_values(1) != headers:
            ws.update("A1", [headers])
        return ws

    def done_keys(self) -> set[str]:
        vals = self.progress.col_values(1)
        return {v for v in vals[1:] if v}

    def known_place_keys(self) -> set[str]:
        idx = CSV_FIELDS.index("place_key") + 1
        vals = self.directory.col_values(idx)
        return {v for v in vals[1:] if v}

    def append_directory(self, records: list[Record]):
        if records:
            self.directory.append_rows([record_row(r) for r in records],
                                       value_input_option="RAW")

    def append_progress(self, entry: dict):
        self.progress.append_row(
            [entry["key"], entry["locality"], entry["niche"], entry["query"],
             entry["found"], entry["added"], entry["scraped_at"]],
            value_input_option="RAW")


def run_sheet(store: SheetStore, queries: list[dict], cfg: ScrapeConfig,
              max_queries: int = 0, log=print) -> dict:
    done = store.done_keys()
    seen = store.known_place_keys()
    todo = [q for q in queries if q["key"] not in done]
    if max_queries:
        todo = todo[:max_queries]
    summary = {"queries_done": 0, "found": 0, "added": 0, "blocked": False}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with MapsScraper(cfg) as scraper:
        for q in todo:
            try:
                records = scraper.scrape_query(q["query"])
            except BlockedError as e:
                log(f"BLOCKED during '{q['query']}': {e} — stopping cleanly; "
                    "the next scheduled run resumes.")
                summary["blocked"] = True
                break
            except Exception as e:
                summary["failed"] = summary.get("failed", 0) + 1
                log(f"ERROR during '{q['query']}': {type(e).__name__}: {e} — "
                    "query NOT marked done; continuing with the next one.")
                if summary["failed"] >= 3 and summary["queries_done"] == 0:
                    log("3 failures before any success — stopping the run.")
                    break
                continue
            new = []
            for rec in records:
                rec.query = q["query"]
                rec.locality = q.get("locality", "")
                rec.niche = q.get("niche", "")
                rec.scraped_at = stamp
                if rec.place_key and rec.place_key not in seen:
                    seen.add(rec.place_key)
                    new.append(rec)
            store.append_directory(new)
            store.append_progress({**q, "found": len(records),
                                   "added": len(new), "scraped_at": stamp})
            summary["queries_done"] += 1
            summary["found"] += len(records)
            summary["added"] += len(new)
            log(f"[{summary['queries_done']}/{len(todo)}] {q['query']}: "
                f"{len(records)} found, {len(new)} new")
            scraper._pause(1.5)
    return summary
