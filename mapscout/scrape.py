"""Google Maps scraping engine (Playwright).

House rules:
- Human-ish pacing with jitter; never solve CAPTCHAs. If Google shows a
  block / unusual-traffic page, raise BlockedError — the caller stops the
  run cleanly with state saved, and the next run resumes.
- Results are appended to the CSV after every query (crash-safe), and the
  dedupe base is the results file itself plus the feature-id key.
"""

from __future__ import annotations

import csv
import json
import os
import random
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import quote_plus, urlparse, unquote

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


class BlockedError(Exception):
    """Google showed a block / unusual-traffic page. Stop, do not solve."""


@dataclass
class ScrapeConfig:
    delay_seconds: float = 3.0     # base pause between actions (jittered)
    max_results: int = 40          # per query
    timeout_seconds: float = 30.0
    headful: bool = False
    base_url: str = "https://www.google.com/maps"


@dataclass
class Record:
    name: str = ""
    category: str = ""
    rating: str = ""
    reviews: str = ""
    address: str = ""
    postcode: str = ""
    town: str = ""
    phone: str = ""
    website: str = ""
    hours: str = ""
    maps_url: str = ""
    place_key: str = ""
    query: str = ""
    locality: str = ""
    niche: str = ""
    scraped_at: str = ""


CSV_FIELDS = ["name", "category", "rating", "reviews", "address", "postcode",
              "town", "phone", "website", "hours", "maps_url", "place_key",
              "query", "locality", "niche", "scraped_at"]

POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b")
FEATURE_ID_RE = re.compile(r"(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)")


def place_key_from_url(url: str) -> str:
    """The stable Maps feature id embedded in a place URL, else ''."""
    m = FEATURE_ID_RE.search(url or "")
    return m.group(1).lower() if m else ""


def fallback_key(name: str, postcode: str, phone: str) -> str:
    base = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return f"{base}|{(postcode or '').upper()}|{re.sub(r'[^0-9]+', '', phone or '')}"


ICON_CHARS = re.compile(r"[\ue000-\uf8ff\u200b\ufeff]")


def clean_text(text: str) -> str:
    """Strip Google Maps icon-font glyphs and tidy whitespace."""
    t = ICON_CHARS.sub("", text or "")
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    return " ".join(lines).strip()


def postcode_from_address(address: str) -> str:
    m = POSTCODE_RE.search((address or "").upper())
    return m.group(1) if m else ""


def town_from_address(address: str, postcode: str) -> str:
    """Town = text sharing the postcode's comma-part ('London SE8 5HD'
    -> 'London'); if the postcode stands alone, the part before it."""
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    parts = [p for p in parts if p.lower() not in ("united kingdom", "uk")]
    if postcode:
        pc = re.compile(re.escape(postcode), re.IGNORECASE)
        for i, p in enumerate(parts):
            if pc.search(p):
                rest = pc.sub("", p).strip(" ,")
                if rest:
                    return rest
                return parts[i - 1] if i > 0 else ""
    return parts[-1] if parts else ""


def _env_proxy() -> dict | None:
    raw = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
           or os.environ.get("all_proxy") or os.environ.get("ALL_PROXY") or "")
    if not raw:
        return None
    u = urlparse(raw)
    if not u.hostname:
        return None
    server = f"{u.scheme or 'http'}://{u.hostname}" + (f":{u.port}" if u.port else "")
    proxy: dict = {"server": server}
    if u.username:
        proxy["username"] = unquote(u.username)
    if u.password:
        proxy["password"] = unquote(u.password)
    bypass = os.environ.get("no_proxy") or os.environ.get("NO_PROXY")
    if bypass:
        proxy["bypass"] = bypass
    return proxy


BLOCK_MARKERS = ("unusual traffic", "not a robot", "recaptcha",
                 "our systems have detected", "/sorry/")


class MapsScraper:
    def __init__(self, cfg: ScrapeConfig):
        self.cfg = cfg
        self._pw = None
        self.browser = None
        self.page = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        kwargs: dict = {"headless": not self.cfg.headful}
        proxy = _env_proxy()
        if proxy:
            kwargs["proxy"] = proxy
        self.browser = self._pw.chromium.launch(**kwargs)
        ctx = self.browser.new_context(
            locale="en-GB",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"))
        self.page = ctx.new_page()
        self.page.set_default_timeout(self.cfg.timeout_seconds * 1000)
        return self

    def __exit__(self, *exc):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    # -- helpers ---------------------------------------------------------
    def _pause(self, mult: float = 1.0):
        base = self.cfg.delay_seconds * mult
        time.sleep(max(0.2, base * random.uniform(0.7, 1.4)))

    def _check_blocked(self):
        url = (self.page.url or "").lower()
        if "/sorry/" in url:
            raise BlockedError(f"Google block page: {url}")
        try:
            text = (self.page.inner_text("body", timeout=3000) or "").lower()
        except Exception:
            return
        if any(m in text for m in BLOCK_MARKERS):
            raise BlockedError("Google unusual-traffic / CAPTCHA page")

    def _consent(self):
        """Dismiss the Google consent interstitial if it appears."""
        try:
            for label in ("Reject all", "Accept all"):
                btn = self.page.get_by_role("button", name=label)
                if btn.count() and btn.first.is_visible():
                    btn.first.click()
                    self._pause()
                    return
        except Exception:
            pass

    # -- search + feed ---------------------------------------------------
    def search(self, query: str):
        url = f"{self.cfg.base_url}/search/{quote_plus(query)}"
        try:
            self.page.goto(url, wait_until="domcontentloaded")
        except PWTimeout:
            self._pause(2)
            self.page.goto(url, wait_until="domcontentloaded")
        self._pause()
        self._consent()
        self._check_blocked()
        # Either a results feed or (rare) a single place panel.
        try:
            self.page.wait_for_selector('div[role="feed"], h1',
                                        timeout=15000)
        except PWTimeout:
            pass
        self._check_blocked()

    def collect_feed(self) -> list[tuple[str, str]]:
        """Scroll the results feed; return [(place_url, card_text)]."""
        page = self.page
        found: dict[str, str] = {}
        feed = page.locator('div[role="feed"]')
        if not feed.count():
            # Single-result search opens the panel directly.
            if page.locator("h1").count():
                return [(page.url, "")]
            return []
        last_count, stale = -1, 0
        while len(found) < self.cfg.max_results and stale < 3:
            cards = page.locator('div[role="feed"] a[href*="/maps/place/"]')
            n = cards.count()
            for i in range(n):
                a = cards.nth(i)
                try:
                    href = a.get_attribute("href") or ""
                    if href and href not in found:
                        card = a.locator("xpath=ancestor::div[1]")
                        try:
                            found[href] = card.inner_text(timeout=1500)
                        except Exception:
                            found[href] = ""
                except Exception:
                    continue
                if len(found) >= self.cfg.max_results:
                    break
            if len(found) == last_count:
                stale += 1
            else:
                stale = 0
            last_count = len(found)
            try:
                feed.evaluate("el => el.scrollBy(0, el.scrollHeight)")
            except Exception:
                break
            self._pause(0.7)
        return list(found.items())[: self.cfg.max_results]

    # -- panel extraction --------------------------------------------------
    def _text(self, selector: str) -> str:
        try:
            loc = self.page.locator(selector)
            if loc.count():
                return clean_text(loc.first.inner_text(timeout=2500) or "")
        except Exception:
            pass
        return ""

    def _attr(self, selector: str, attr: str) -> str:
        try:
            loc = self.page.locator(selector)
            if loc.count():
                return (loc.first.get_attribute(attr) or "").strip()
        except Exception:
            pass
        return ""

    def extract_panel(self, url: str, card_text: str = "") -> Record:
        page = self.page
        page.goto(url, wait_until="domcontentloaded")
        self._pause()
        self._check_blocked()
        try:
            page.wait_for_selector("h1", timeout=12000)
        except PWTimeout:
            pass
        rec = Record()
        rec.name = self._text("h1")
        rec.maps_url = page.url.split("?")[0] if "?" in page.url else page.url
        rec.place_key = place_key_from_url(url) or place_key_from_url(page.url)
        # Category: the small button/span under the title.
        rec.category = (self._text('button[jsaction*="category"]')
                        or self._text('span[jsaction*="category"]'))
        # Rating + reviews from the rating row.
        rating_row = self._text('div[role="img"][aria-label*="stars"]')
        m = re.search(r"([0-9.]+)\s*stars?", rating_row)
        if m:
            rec.rating = m.group(1)
        if not rec.rating:
            m = re.search(r"\b([0-5]\.[0-9])\b", self._text("div.F7nice") or "")
            if m:
                rec.rating = m.group(1)
        aria = self._attr('div[role="img"][aria-label*="reviews"]', "aria-label")
        m = re.search(r"([\d,]+)\s*reviews?", aria)
        if m:
            rec.reviews = m.group(1).replace(",", "")
        if not rec.reviews:
            row_text = self._text("div.F7nice") or rating_row
            m = re.search(r"\(([\d,]+)\)", row_text)
            if m:
                rec.reviews = m.group(1).replace(",", "")
        rec.address = self._text('button[data-item-id="address"]')
        rec.phone = self._text('button[data-item-id^="phone:tel:"]')
        rec.website = (self._attr('a[data-item-id="authority"]', "href")
                       or self._text('a[data-item-id="authority"]'))
        hours_lbl = (self._attr('button[data-item-id*="hours"]', "aria-label")
                     or self._attr('div[data-item-id*="hours"]', "aria-label")
                     or self._attr('button[aria-label*="Closes"]', "aria-label")
                     or self._attr('button[aria-label*="Opens"]', "aria-label")
                     or self._attr('div[aria-label*="Closes"]', "aria-label")
                     or self._attr('div[aria-label*="Opens"]', "aria-label"))
        rec.hours = re.sub(r"\s+", " ", clean_text(hours_lbl)).strip()
        if card_text:
            self._fill_from_card(rec, card_text)
        rec.postcode = postcode_from_address(rec.address)
        rec.town = town_from_address(rec.address, rec.postcode)
        if not rec.place_key:
            rec.place_key = fallback_key(rec.name, rec.postcode, rec.phone)
        return rec

    def _fill_from_card(self, rec: Record, card_text: str):
        """Fill gaps from the feed card's text (name/rating/category lines)."""
        lines = [ln.strip() for ln in card_text.splitlines() if ln.strip()]
        if not rec.name and lines:
            rec.name = lines[0]
        m = re.search(r"\b([0-5]\.[0-9])\s*\(([\d,]+)\)", card_text)
        if m:
            if not rec.rating:
                rec.rating = m.group(1)
            if not rec.reviews:
                rec.reviews = m.group(2).replace(",", "")
        if not rec.category and len(lines) > 1:
            for ln in lines[1:]:
                if "·" in ln:
                    rec.category = ln.split("·")[0].strip()
                    break

    def scrape_query(self, query: str) -> list[Record]:
        self.search(query)
        pairs = self.collect_feed()
        records = []
        for url, card_text in pairs:
            self._pause(0.6)
            try:
                records.append(self.extract_panel(url, card_text))
            except BlockedError:
                raise
            except Exception:
                continue
        return records


# -- files / state -------------------------------------------------------

def load_existing_keys(out_csv: Path) -> set[str]:
    keys: set[str] = set()
    if out_csv.exists():
        with open(out_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("place_key"):
                    keys.add(row["place_key"])
    return keys


def append_records(out_csv: Path, records: list[Record]):
    exists = out_csv.exists()
    with open(out_csv, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            w.writeheader()
        for rec in records:
            w.writerow(asdict(rec))


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"done_queries": [], "blocked": False}


def save_state(path: Path, state: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(path)


def run_queries(queries: list[dict], out_csv: Path, state_path: Path,
                cfg: ScrapeConfig, max_queries: int = 0,
                log=print) -> dict:
    """queries: [{key, query, locality, niche}]. Resumable via state file."""
    state = load_state(state_path)
    state["blocked"] = False
    done = set(state.get("done_queries", []))
    seen_keys = load_existing_keys(out_csv)
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
                    "progress saved, next run resumes.")
                state["blocked"] = True
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
                rec.query, rec.locality, rec.niche = q["query"], q.get("locality", ""), q.get("niche", "")
                rec.scraped_at = stamp
                if rec.place_key and rec.place_key not in seen_keys:
                    seen_keys.add(rec.place_key)
                    new.append(rec)
            if new:
                append_records(out_csv, new)
            done.add(q["key"])
            state["done_queries"] = sorted(done)
            save_state(state_path, state)
            summary["queries_done"] += 1
            summary["found"] += len(records)
            summary["added"] += len(new)
            log(f"[{summary['queries_done']}/{len(todo)}] {q['query']}: "
                f"{len(records)} found, {len(new)} new")
            scraper._pause(1.5)
    return summary
