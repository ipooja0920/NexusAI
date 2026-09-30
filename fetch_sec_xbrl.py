#!/usr/bin/env python3
"""Fetch R&D, revenue, cash and short-term investments from SEC XBRL frames
->  data/sources/xbrl/

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

Two kinds of concept:

  FLOW  - money over a whole year (R&D expense, revenue).
          Frame period CY2025 = calendar year 2025.
  STOCK - money held on one day (cash, short-term investments), read off
          the balance sheet. Frame period CY2026Q2I = as at 30 June 2026
          (the trailing "I" means "instant"). Every 10-Q has a balance
          sheet, so this is at most a quarter old - fresher than any
          annual figure.

Cash is the closest thing SEC publishes to "money it could spend on a
sponsored-research agreement". Short-term investments are kept separate
because biotechs often park most of their money there rather than in cash.

Input:  nothing - it downloads what it needs
Output: data/sources/xbrl/frames_<concept>_<period>.json  raw API response,
                                                         read by clean_xbrl.py
        data/sources/xbrl/<concept>_CY<year>.csv   flow concepts only, for
                                                   the older enrich_xbrl.py
        data/sources/xbrl/company_tickers.json     SEC ticker -> CIK map

Usage:
    python fetch_sec_xbrl.py                    # CY2023-CY2025 + last 4 quarter-ends
    python fetch_sec_xbrl.py --years 2024 2025
    python fetch_sec_xbrl.py --quarters 2026Q1 2026Q2
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
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_DIR = PROJECT_ROOT / "data" / "sources" / "xbrl"

FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/{period}.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
TAG = "ResearchAndDevelopmentExpense"

# Concepts fetched when no --tag is given. R&D has one dominant tag, but
# revenue does NOT: companies split between the older "Revenues" and the
# ASC 606 "RevenueFromContractWithCustomer..." tags, and asking for the
# wrong one returns 404 rather than zero. Verified against real filings -
# Kymera 404s on Revenues but reports under the ASC 606 tag; Organogenesis
# does the opposite. So both are fetched and enrich_xbrl.py resolves per
# company. Order here is the priority order used there.
FLOW_TAGS = [
    "ResearchAndDevelopmentExpense",
    # Some companies report R&D only under this narrower tag, which leaves
    # out research bought in through acquisitions. Fallback only.
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]

# Balance-sheet concepts. Same story as revenue: each company's accountants
# pick one tag, so several are fetched and clean_xbrl.py takes the first a
# company uses. They are alternatives, never added together - a company
# reporting both ShortTermInvestments and MarketableSecuritiesCurrent is
# usually describing the same money twice.
INSTANT_TAGS = [
    "CashAndCashEquivalentsAtCarryingValue",
    # includes restricted cash - slightly higher; used only if the plain
    # figure is missing
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "ShortTermInvestments",
    "MarketableSecuritiesCurrent",
    "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    # less common investment labels - a company that moved its cash into
    # these would otherwise look as if the money had vanished
    "DebtSecuritiesHeldToMaturityAmortizedCostAfterAllowanceForCreditLossCurrent",
    "HeldToMaturitySecuritiesCurrent",
    "OtherShortTermInvestments",
    "AvailableForSaleSecuritiesCurrent",
    # the company's OWN total of cash + short-term investments, used as a
    # check on our sum (clean_xbrl.py / combine_companies.py)
    "CashCashEquivalentsAndShortTermInvestments",
]

DEFAULT_TAGS = FLOW_TAGS + INSTANT_TAGS

#: Short filename stem per tag, so the CSVs stay readable.
TAG_SLUG = {
    "ResearchAndDevelopmentExpense": "rd_expense",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue_asc606",
    "Revenues": "revenue_legacy",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "revenue_asc606_incl",
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost": "rd_expense_excl_iprd",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": "cash_incl_restricted",
    "ShortTermInvestments": "short_term_investments",
    "MarketableSecuritiesCurrent": "marketable_securities_current",
    "AvailableForSaleSecuritiesDebtSecuritiesCurrent": "afs_debt_securities_current",
    "DebtSecuritiesHeldToMaturityAmortizedCostAfterAllowanceForCreditLossCurrent": "htm_debt_securities_current",
    "HeldToMaturitySecuritiesCurrent": "htm_securities_current",
    "OtherShortTermInvestments": "other_short_term_investments",
    "AvailableForSaleSecuritiesCurrent": "afs_securities_current",
    "CashCashEquivalentsAndShortTermInvestments": "cash_and_short_term_investments",
}

DEFAULT_YEARS = [2023, 2024, 2025]

# A frame keeps filling up as late filers file: most companies file within
# 45 days of a quarter-end and 90 days of a year-end, a few later. A copy
# saved sooner than this after its period ended is fetched again next run;
# one saved later is final and reused for ever.
FINAL_AFTER_DAYS = 120


def quarter_end(year: int, q: int) -> date:
    return {1: date(year, 3, 31), 2: date(year, 6, 30),
            3: date(year, 9, 30), 4: date(year, 12, 31)}[q]


def default_quarters(today: Optional[date] = None, n: int = 4) -> list:
    """The last n quarter-ends strictly before today, oldest first,
    as (year, quarter). On 30 Sep 2026: 2025Q3, 2025Q4, 2026Q1, 2026Q2."""
    today = today or date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    while len(out) < n:
        if quarter_end(y, q) < today:
            out.append((y, q))
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return sorted(out)


def parse_quarter(text: str) -> tuple:
    """'2026Q2' -> (2026, 2)."""
    y, _, q = text.upper().partition("Q")
    if not (y.isdigit() and q in ("1", "2", "3", "4")):
        raise argparse.ArgumentTypeError(f"expected e.g. 2026Q2, got {text!r}")
    return int(y), int(q)

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


def load_or_fetch(path: Path, url: str, refresh: bool,
                  final_on: Optional[date] = None) -> Optional[dict]:
    """Fetch a URL, or reuse the copy already on disk.

    A finished period never changes, so re-downloading it is pure waste -
    the same reason geocodes and embeddings are cached. `final_on` is the
    date a period stops changing; a copy saved before it is refetched.
    """
    if path.exists() and not refresh:
        saved = date.fromtimestamp(path.stat().st_mtime)
        if final_on is None or saved >= final_on:
            print(f"  using cached {path.name} - pass --refresh to refetch")
            return json.loads(path.read_text(encoding="utf-8"))
        print(f"  {path.name} was saved {saved}, before the period was "
              f"final ({final_on}) - refetching")

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


def periods_for(tag: str, years: list, quarters: list) -> list:
    """(period label, year, date the period ends) to fetch for one tag.
    Flow tags are asked for by calendar year, balance-sheet tags by
    quarter-end."""
    if tag in INSTANT_TAGS:
        return [(f"CY{y}Q{q}I", y, quarter_end(y, q)) for y, q in quarters]
    return [(f"CY{y}", y, date(y, 12, 31)) for y in sorted(years)]


def fetch_tag(tag: str, years: list, quarters: list, tickers: dict,
              refresh: bool) -> list:
    """Fetch one us-gaap concept for each period. Returns summary rows."""
    slug = TAG_SLUG.get(tag, tag)
    summary = []
    print(f"\n--- {tag} ---")
    for period, year, ends in periods_for(tag, years, quarters):
        raw_path = SOURCE_DIR / f"frames_{slug}_{period}.json"
        frame = load_or_fetch(raw_path, FRAMES.format(tag=tag, period=period),
                              refresh, ends + timedelta(days=FINAL_AFTER_DAYS))
        if frame is None:
            print(f"  {period}: not published (this tag is unused, or the "
                  "period is too recent) - skipped")
            summary.append((tag, period, 0, "not published"))
            continue

        pts = frame.get("pts", len(frame.get("data", [])))
        note = ""
        if pts < SPARSE_WARNING:
            note = "INCOMPLETE - period too recent"
            print(f"  {period}: WARNING - only {pts:,} companies reported.")
            print("    Companies file 45 days after a quarter ends and 60-90")
            print("    after a year ends. It will be refetched on a later run.")

        if tag in INSTANT_TAGS:
            print(f"  {period}: {pts:>6,} companies -> {raw_path.name}")
        else:
            # The CSV is only for the older enrich_xbrl.py; clean_xbrl.py
            # reads the JSON directly.
            csv_path = SOURCE_DIR / f"{slug}_CY{year}.csv"
            write_csv(csv_path, frame_to_rows(frame, year, tickers))
            print(f"  {period}: {pts:>6,} companies -> {csv_path.name}")
        summary.append((tag, period, pts, note))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fetch SEC XBRL frames -> one CSV per concept per year.")
    ap.add_argument("--years", type=int, nargs="+", default=DEFAULT_YEARS,
                    help=f"Calendar years to fetch (default: {DEFAULT_YEARS})")
    ap.add_argument("--quarters", type=parse_quarter, nargs="+", default=None,
                    help="Quarter-ends for cash / short-term investments, "
                         "e.g. 2026Q1 2026Q2 (default: the last 4)")
    ap.add_argument("--tag", nargs="+", default=None,
                    help="us-gaap concept(s) to fetch. Default: "
                         + ", ".join(DEFAULT_TAGS))
    ap.add_argument("--refresh", action="store_true",
                    help="Ignore cached JSON and refetch from SEC.")
    args = ap.parse_args()

    tags = args.tag or DEFAULT_TAGS
    quarters = sorted(args.quarters) if args.quarters else default_quarters()

    print("\n=== NexusAI: SEC XBRL fetch ===")
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    print("\nLoading SEC ticker -> CIK map...")
    tickers = load_ticker_map(args.refresh)

    summary = []
    for tag in tags:
        summary.extend(fetch_tag(tag, args.years, quarters, tickers,
                                 args.refresh))

    print("\n" + "=" * 70)
    print(f"  {'concept':<32}{'period':>11}{'companies':>12}")
    for tag, period, pts, note in summary:
        print(f"  {TAG_SLUG.get(tag, tag):<32}{period:>11}{pts:>12,}  {note}")
    print(f"\n  Written to: {SOURCE_DIR}")
    print("  Finished periods never change - re-running reuses the cached JSON.")
    print("  Next: python clean_xbrl.py")
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
