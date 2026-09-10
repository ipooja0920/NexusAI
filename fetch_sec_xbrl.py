#!/usr/bin/env python3
"""Fetch R&D expense from SEC XBRL frames  ->  data/sources/xbrl/

Capital IQ tells you how big a company is (revenue, headcount). It does not
tell you how much of that it spends on research. For a faculty partnership
that is the more relevant number: a company putting $200m a year into R&D is
a research organisation whatever its revenue looks like.

XBRL only covers SEC registrants, so this reaches the ~180 publicly listed
companies in the datasource, not the ~2,300 private ones. It is a QUALITY
improvement on companies we already know about, not coverage of the ones we
do not - Form D and SBIR are what reach those.

How the frames API works:
  one call = one accounting concept, one calendar period, EVERY filer.
  So three years of R&D expense is three small calls, not one call per
  company. Each response is under half a megabyte.

  https://data.sec.gov/api/xbrl/frames/us-gaap/<Tag>/USD/CY2024.json

Why only R&D and not revenue: revenue is fragmented across several tags
(Revenues, RevenueFromContractWithCustomerExcludingAssessedTax, ...) and
none is complete, and it would fill revenue for only ~24 companies in the
datasource. R&D expense has one dominant tag and applies to all of them.

Input:  nothing - it downloads what it needs
Output: data/sources/xbrl/rd_expense_CY<year>.csv   one row per company
        data/sources/xbrl/frames_rd_CY<year>.json   raw API response (cached)
        data/sources/xbrl/company_tickers.json      SEC ticker -> CIK map

Usage:
    python fetch_sec_xbrl.py                    # CY2023-CY2025
    python fetch_sec_xbrl.py --years 2024 2025
    python fetch_sec_xbrl.py --refresh          # ignore cache, refetch
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_DIR = PROJECT_ROOT / "data" / "sources" / "xbrl"

FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/CY{year}.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
TAG = "ResearchAndDevelopmentExpense"

# Concepts fetched when no --tag is given. R&D has one dominant tag, but
# revenue does NOT: companies split between the older "Revenues" and the
# ASC 606 "RevenueFromContractWithCustomer..." tags, and asking for the
# wrong one returns 404 rather than zero. Verified against real filings -
# Kymera 404s on Revenues but reports under the ASC 606 tag; Organogenesis
# does the opposite. So both are fetched and enrich_xbrl.py resolves per
# company. Order here is the priority order used there.
DEFAULT_TAGS = [
    "ResearchAndDevelopmentExpense",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]

#: Short filename stem per tag, so the CSVs stay readable.
TAG_SLUG = {
    "ResearchAndDevelopmentExpense": "rd_expense",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue_asc606",
    "Revenues": "revenue_legacy",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "revenue_asc606_incl",
}

DEFAULT_YEARS = [2023, 2024, 2025]

# SEC blocks requests without a User-Agent that identifies the caller, and
# returns 403 rather than anything explanatory. This is their documented
# requirement, not an attempt to look like a browser.
CONTACT = "lakshmipoojaraj@gmail.com"
HEADERS = {
    "User-Agent": f"NexusAI-UConn ({CONTACT})",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
}
PAUSE = 0.2          # SEC asks for <= 10 requests/second; we make about 4 calls

# A calendar-year frame is only meaningful once companies have filed their
# annual reports, which happens 60-90 days after their year ends. Asking for
# the current year returns a near-empty frame rather than an error, so warn
# instead of silently writing a 20-row CSV.
SPARSE_WARNING = 500


def _get_json(url: str, retries: int = 4) -> Optional[dict]:
    """GET with retry/backoff. Returns None on 404 (frame does not exist)."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None                      # no such frame - caller decides
            if e.code == 403:
                raise RuntimeError(
                    "SEC returned 403 Forbidden. This almost always means the "
                    "User-Agent header was missing or rejected - SEC requires "
                    "one identifying you. Current value:\n"
                    f"  {HEADERS['User-Agent']}"
                ) from e
            last = e
        except Exception as e:                   # noqa: BLE001
            last = e
        wait = 2 ** attempt
        print(f"    [retry {attempt}/{retries}] {last} - waiting {wait}s")
        time.sleep(wait)
    raise RuntimeError(f"giving up after {retries} attempts: {last}")


def load_or_fetch(path: Path, url: str, refresh: bool) -> Optional[dict]:
    """Fetch a URL, or reuse the copy already on disk.

    A completed calendar year never changes, so re-downloading it is pure
    waste - the same reason geocodes and embeddings are cached.
    """
    if path.exists() and not refresh:
        print(f"  using cached {path.name} - pass --refresh to refetch")
        return json.loads(path.read_text(encoding="utf-8"))

    data = _get_json(url)
    if data is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    time.sleep(PAUSE)
    return data


def load_ticker_map(refresh: bool) -> dict:
    """CIK -> (ticker, title). Lets the CSV carry a ticker, which is how we
    will later join to Capital IQ names like 'Adicet Bio, Inc. (NasdaqCM:ACET)'
    - an exact identifier match rather than a fuzzy name match."""
    path = SOURCE_DIR / "company_tickers.json"
    data = load_or_fetch(path, TICKERS, refresh)
    if not data:
        print("  WARNING: could not load company_tickers.json - "
              "CSVs will have no ticker column filled in")
        return {}
    # A CIK can carry several tickers - common stock plus preferred shares,
    # warrants and rights (CIK 1652044 is GOOGL, GOOG, GOOGM and GOOGN;
    # Ford is F, F-PB, F-PC, F-PD). 1,441 of 8,013 CIKs are like this.
    # Keeping whichever arrived last stored "GOOGN" for Alphabet, which
    # matches nothing. Prefer the common-stock ticker: no dash, then
    # shortest, then alphabetical.
    def rank(t: str) -> tuple:
        return ("-" in t, len(t), t)

    best = {}
    for row in data.values():
        try:
            cik, tick = int(row["cik_str"]), (row.get("ticker") or "").upper()
            title = row.get("title")
        except (KeyError, TypeError, ValueError):
            continue
        if not tick:
            continue
        if cik not in best or rank(tick) < rank(best[cik][0]):
            best[cik] = (tick, title)

    multi = len(data) - len(best)
    print(f"  ticker map: {len(best):,} companies"
          + (f" ({multi:,} extra tickers collapsed to the common share)"
             if multi else ""))
    return best


COLUMNS = ["cik", "cik_padded", "ticker", "entity_name", "sec_title",
           "location", "fiscal_year", "period_start", "period_end",
           "rd_expense_usd", "accession"]


def frame_to_rows(frame: dict, year: int, tickers: dict) -> list:
    rows = []
    for d in frame.get("data", []):
        cik = d.get("cik")
        tick, title = tickers.get(cik, (None, None))
        rows.append({
            "cik": cik,
            "cik_padded": f"{cik:010d}" if isinstance(cik, int) else "",
            "ticker": tick or "",
            "entity_name": d.get("entityName"),
            "sec_title": title or "",
            "location": d.get("loc"),
            "fiscal_year": year,
            "period_start": d.get("start"),
            "period_end": d.get("end"),
            "rd_expense_usd": d.get("val"),
            "accession": d.get("accn"),
        })
    rows.sort(key=lambda r: (r["rd_expense_usd"] or 0), reverse=True)
    return rows


def write_csv(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
    except PermissionError as e:
        raise PermissionError(
            f"Could not write {path.name} - is it open in Excel? Close it and "
            "re-run (the raw JSON is cached, so it costs nothing)."
        ) from e


def fetch_tag(tag: str, years: list, tickers: dict, refresh: bool) -> list:
    """Fetch one us-gaap concept for each year. Returns summary rows."""
    slug = TAG_SLUG.get(tag, tag)
    summary = []
    print(f"\n--- {tag} ---")
    for year in sorted(years):
        raw_path = SOURCE_DIR / f"frames_{slug}_CY{year}.json"
        frame = load_or_fetch(raw_path, FRAMES.format(tag=tag, year=year),
                              refresh)
        if frame is None:
            print(f"  CY{year}: no frame published yet - skipped")
            summary.append((tag, year, 0, 0, "not published"))
            continue

        pts = frame.get("pts", len(frame.get("data", [])))
        rows = frame_to_rows(frame, year, tickers)
        with_ticker = sum(1 for r in rows if r["ticker"])

        note = ""
        if pts < SPARSE_WARNING:
            note = "INCOMPLETE - year not finished"
            print(f"  CY{year}: WARNING - only {pts:,} companies reported.")
            print("    A calendar year is not usable until companies have")
            print("    filed annual reports, 60-90 days after their year ends.")

        csv_path = SOURCE_DIR / f"{slug}_CY{year}.csv"
        write_csv(csv_path, rows)
        print(f"  CY{year}: {pts:>6,} companies -> {csv_path.name}")
        summary.append((tag, year, pts, with_ticker, note))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fetch SEC XBRL frames -> one CSV per concept per year.")
    ap.add_argument("--years", type=int, nargs="+", default=DEFAULT_YEARS,
                    help=f"Calendar years to fetch (default: {DEFAULT_YEARS})")
    ap.add_argument("--tag", nargs="+", default=None,
                    help="us-gaap concept(s) to fetch. Default: "
                         + ", ".join(DEFAULT_TAGS))
    ap.add_argument("--refresh", action="store_true",
                    help="Ignore cached JSON and refetch from SEC.")
    args = ap.parse_args()

    tags = args.tag or DEFAULT_TAGS

    print("\n=== NexusAI: SEC XBRL fetch ===")
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    print("\nLoading SEC ticker -> CIK map...")
    tickers = load_ticker_map(args.refresh)

    summary = []
    for tag in tags:
        summary.extend(fetch_tag(tag, args.years, tickers, args.refresh))

    print("\n" + "=" * 70)
    print(f"  {'concept':<46}{'year':>6}{'companies':>12}")
    for tag, year, pts, _, note in summary:
        print(f"  {TAG_SLUG.get(tag, tag):<46}{year:>6}{pts:>12,}  {note}")
    print(f"\n  Written to: {SOURCE_DIR}")
    print("  Completed years never change - re-running reuses the cached JSON.")
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
