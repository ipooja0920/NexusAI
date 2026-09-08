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
    out = {}
    for row in data.values():
        try:
            out[int(row["cik_str"])] = (row.get("ticker"), row.get("title"))
        except (KeyError, TypeError, ValueError):
            continue
    print(f"  ticker map: {len(out):,} public companies")
    return out


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


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fetch SEC XBRL R&D expense frames -> one CSV per year.")
    ap.add_argument("--years", type=int, nargs="+", default=DEFAULT_YEARS,
                    help=f"Calendar years to fetch (default: {DEFAULT_YEARS})")
    ap.add_argument("--tag", default=TAG,
                    help=f"us-gaap concept to fetch (default: {TAG})")
    ap.add_argument("--refresh", action="store_true",
                    help="Ignore cached JSON and refetch from SEC.")
    args = ap.parse_args()

    print(f"\n=== NexusAI: SEC XBRL fetch [{args.tag}] ===\n")
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading SEC ticker -> CIK map...")
    tickers = load_ticker_map(args.refresh)
    print()

    short_tag = "rd_expense" if args.tag == TAG else args.tag
    summary = []

    for year in sorted(args.years):
        print(f"CY{year}:")
        raw_path = SOURCE_DIR / f"frames_{short_tag}_CY{year}.json"
        frame = load_or_fetch(raw_path, FRAMES.format(tag=args.tag, year=year),
                              args.refresh)
        if frame is None:
            print(f"  no frame published for CY{year} yet - skipped\n")
            summary.append((year, 0, 0, "not published"))
            continue

        pts = frame.get("pts", len(frame.get("data", [])))
        rows = frame_to_rows(frame, year, tickers)
        with_ticker = sum(1 for r in rows if r["ticker"])

        note = ""
        if pts < SPARSE_WARNING:
            note = "INCOMPLETE - year not finished"
            print(f"  WARNING: only {pts:,} companies reported for CY{year}.")
            print("  A calendar year is not usable until companies have filed")
            print("  their annual reports, 60-90 days after their year ends.")

        csv_path = SOURCE_DIR / f"{short_tag}_CY{year}.csv"
        write_csv(csv_path, rows)
        print(f"  {pts:,} companies  ({with_ticker:,} matched to a ticker)")
        print(f"  -> {csv_path.name}  ({csv_path.stat().st_size/1024:.0f} KB)\n")
        summary.append((year, pts, with_ticker, note))

    print("=" * 64)
    print(f"  {'year':<8}{'companies':>12}{'with ticker':>14}   note")
    for year, pts, tick, note in summary:
        print(f"  CY{year:<6}{pts:>12,}{tick:>14,}   {note}")
    print(f"\n  Written to: {SOURCE_DIR}")
    print("  Completed years never change - re-running reuses the cached JSON.")
    print("=" * 64 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
