# mapscout

A slow, resumable **Google Maps business scraper** that builds a local
business directory — built to run unattended in short scheduled sittings
(GitHub Actions) and to feed a Google Sheet.

It searches Google Maps **locality by locality × niche by niche**
("tyre shop in Sheffield", "MOT test centre in Leeds", …) using a bundled
list of **6,369 UK populated places** (GeoNames, ordered by population) and
26 automotive niches, opens every result, and records:

`name, category, rating, reviews, address, postcode, town, phone, website,
hours, maps_url, place_key, query, locality, niche, scraped_at`

## House rules

- **Slow by design.** Human-ish pacing with jitter — speed is what gets
  scrapers blocked.
- **Never solves CAPTCHAs.** If Google shows a block / unusual-traffic
  page, the run stops cleanly with all progress saved (exit code 3). The
  next run resumes exactly where it stopped.
- **Dedupes by the place's stable Maps feature id**, so a business found
  by five different searches enters the directory once.
- **Crash-safe.** Results are written after every single query.

## Storage modes

1. **Local CSV** — `scrape` writes a CSV + a JSON state file.
2. **Google Sheet** — `sheet-run` writes to a shared spreadsheet with two
   tabs: `Directory` (businesses) and `Progress` (one row per completed
   query = the resumable state). Access is via a Google **service
   account** whose JSON key you keep in a secret; share the sheet with
   the service account's email as Editor.

## Usage

```bash
pip install -r requirements.txt
python -m playwright install chromium

# build a query matrix
python -m mapscout make-queries -o queries.csv --min-population 5000

# scrape into a local CSV (resumable)
python -m mapscout scrape --queries queries.csv -o directory.csv

# scrape into a Google Sheet (needs requirements-sheet.txt + a key)
pip install -r requirements-sheet.txt
python -m mapscout sheet-run --sheet-id <SPREADSHEET_ID> --sa-key key.json --max-queries 20
```

## Scheduled runs (GitHub Actions)

`.github/workflows/scrape.yml` runs every 4 hours (and on demand) with
two repository secrets: `GOOGLE_SA_KEY_JSON` and `DIRECTORY_SHEET_ID`.
A concurrency lock prevents overlapping runs from double-processing.

## Tests

```bash
python -m unittest discover -s tests
```

The end-to-end tests run against a local fixture site that mimics the
Google Maps DOM (feed, place panels, block page) — no network needed.

## Honest limitations

- Google Maps UI selectors change over time; the extractor uses several
  fallbacks per field, but expect occasional maintenance.
- Datacenter IPs get blocked sooner than residential ones. The design
  answer is pacing + resumability, not evasion.
- Localities are GeoNames places with a recorded population (> 0).

## License

MIT
