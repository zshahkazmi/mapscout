"""mapscout CLI.

  python -m mapscout scrape --queries queries.csv -o directory.csv
  python -m mapscout make-queries --localities data/localities_gb.csv \
      --niches mapscout/data/niches.txt -o queries.csv
  python -m mapscout sheet-run --sheet-id <ID> --sa-key key.json

Exit codes: 0 = finished the requested work; 3 = stopped early on a
Google block (progress saved; safe to run again later).
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .scrape import ScrapeConfig, run_queries
from .sheet import generate_queries, load_niches

DATA_DIR = Path(__file__).parent / "data"


def _cfg(args) -> ScrapeConfig:
    return ScrapeConfig(delay_seconds=args.delay,
                        max_results=args.max_results,
                        headful=args.headful,
                        base_url=args.base_url)


def cmd_scrape(args) -> int:
    queries = []
    with open(args.queries, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            q = (r.get("query") or "").strip()
            if not q:
                continue
            queries.append({
                "key": (r.get("key") or f"{r.get('niche', '')}|{r.get('locality', '')}").strip("|") or q,
                "query": q,
                "locality": r.get("locality", ""),
                "niche": r.get("niche", ""),
            })
    if not queries:
        print("no queries in file", file=sys.stderr)
        return 2
    summary = run_queries(queries, Path(args.output), Path(args.state),
                          _cfg(args), max_queries=args.max_queries)
    print(f"done: {summary}")
    return 3 if summary["blocked"] else 0


def cmd_make_queries(args) -> int:
    niches = load_niches(Path(args.niches))
    queries = generate_queries(Path(args.localities), niches,
                                 min_population=args.min_population,
                                 limit_localities=args.limit_localities)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["key", "locality", "niche", "query"])
        w.writeheader()
        w.writerows(queries)
    print(f"wrote {len(queries)} queries to {args.output}")
    return 0


def cmd_sheet_run(args) -> int:
    from .sheet import SheetStore, run_sheet
    niches = load_niches(Path(args.niches))
    queries = generate_queries(Path(args.localities), niches,
                                 min_population=args.min_population)
    store = SheetStore(args.sa_key, args.sheet_id)
    summary = run_sheet(store, queries, _cfg(args), max_queries=args.max_queries)
    print(f"done: {summary}")
    return 3 if summary["blocked"] else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mapscout")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--max-queries", type=int, default=0)
        sp.add_argument("--max-results", type=int, default=40)
        sp.add_argument("--delay", type=float, default=3.0)
        sp.add_argument("--headful", action="store_true")
        sp.add_argument("--base-url", default="https://www.google.com/maps")

    sp = sub.add_parser("scrape", help="scrape a queries CSV into a local CSV")
    sp.add_argument("--queries", required=True)
    sp.add_argument("-o", "--output", default="directory.csv")
    sp.add_argument("--state", default="mapscout_state.json")
    common(sp)
    sp.set_defaults(fn=cmd_scrape)

    sp = sub.add_parser("make-queries", help="build the locality x niche matrix")
    sp.add_argument("--localities", default=str(DATA_DIR / "localities_gb.csv"))
    sp.add_argument("--niches", default=str(DATA_DIR / "niches.txt"))
    sp.add_argument("-o", "--output", default="queries.csv")
    sp.add_argument("--min-population", type=int, default=0)
    sp.add_argument("--limit-localities", type=int, default=0)
    sp.set_defaults(fn=cmd_make_queries)

    sp = sub.add_parser("sheet-run", help="scrape into a Google Sheet (state = Progress tab)")
    sp.add_argument("--sheet-id", required=True)
    sp.add_argument("--sa-key", required=True, help="service account JSON key file")
    sp.add_argument("--localities", default=str(DATA_DIR / "localities_gb.csv"))
    sp.add_argument("--niches", default=str(DATA_DIR / "niches.txt"))
    sp.add_argument("--min-population", type=int, default=0)
    common(sp)
    sp.set_defaults(fn=cmd_sheet_run)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
