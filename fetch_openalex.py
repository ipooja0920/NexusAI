#!/usr/bin/env python3
"""Fetch US funders and US company institutions from OpenAlex -> Excel.

Standalone exploration tool. Does NOT touch the matching pipeline - it just
pulls the two OpenAlex indexes we care about and writes them to a spreadsheet
so you can look at what is actually in there.

  python fetch_openalex.py                  # fetch both, write Excel
  python fetch_openalex.py --funders-only
  python fetch_openalex.py --refresh        # ignore the raw cache, refetch

Why these two indexes:
  funders      -> "has this company paid for academic research?"
  institutions -> "does this company publish research?"
Both are small enough (about 20k and 40k US records) to download whole and
match locally, so no per-company queries are ever needed.

API key: optional but strongly recommended - it raises the daily budget from
$0.10 to $1. Get one free at openalex.org/settings/api and put it in .env as
  OPENALEX_API_KEY=your_key_here
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

PROJECT_ROOT = Path(__file__).resolve().parent

# Everything this script produces is a DOWNLOAD, not a computed result, so
# it lives in data/sources/ rather than data/cache/. The practical payoff:
# you can wipe data/cache/ to force a clean rebuild without throwing away
# the ~200MB fetched from the API here.
#
# Matching this against our own companies is a different job - that IS
# computed, so it will write data/cache/openalex.json plus a review sheet
# in data/review/, the same shape the Form D enrichment uses.
SOURCE_DIR = PROJECT_ROOT / "data" / "sources" / "openalex"
OUT_XLSX = SOURCE_DIR / "openalex_us.xlsx"

API = "https://api.openalex.org"
PER_PAGE = 100          # documented maximum
CONTACT = "lakshmipoojaraj@gmail.com"   # polite-pool contact
PAUSE = 0.15            # seconds between pages; well inside 100 req/sec

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)


# ----------------------------------------------------------------------
# HTTP (stdlib only - no requests dependency)
# ----------------------------------------------------------------------

def _get_json(url: str, retries: int = 4) -> dict:
    """GET with retry/backoff. Raises on final failure."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": f"NexusAI-UConn ({CONTACT})",
                              "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:                      # noqa: BLE001
            last = e
            code = getattr(e, "code", None)
            if code in (400, 401, 403):             # not worth retrying
                raise
            wait = 2 ** attempt
            print(f"    [retry {attempt}/{retries}] {e} - waiting {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"giving up after {retries} attempts: {last}")


def fetch_all(entity: str, filters: str, api_key: Optional[str]) -> list:
    """Cursor-paginate an OpenAlex endpoint and return every record."""
    records, cursor, page_no = [], "*", 0
    while cursor:
        params = {
            "filter": filters,
            "per-page": str(PER_PAGE),
            "cursor": cursor,
            "mailto": CONTACT,
        }
        if api_key:
            params["api_key"] = api_key
        url = f"{API}/{entity}?" + urllib.parse.urlencode(params)

        data = _get_json(url)
        batch = data.get("results", [])
        records.extend(batch)
        page_no += 1

        meta = data.get("meta", {})
        total = meta.get("count")
        if page_no == 1:
            print(f"  total {entity} matching filter: {total:,}")
        if page_no % 10 == 0 or not batch:
            print(f"    page {page_no}: {len(records):,}/{total:,} fetched")

        cursor = meta.get("next_cursor")
        if not batch:
            break
        time.sleep(PAUSE)

    print(f"  done: {len(records):,} {entity} records")
    return records


# ----------------------------------------------------------------------
# Field extraction
# ----------------------------------------------------------------------

def summarise(rec: dict, this_year: int) -> dict:
    """Flatten one OpenAlex record into the columns we care about.

    counts_by_year contains junk future years in the source data (the NSF
    record has an entry for year 2604), so years are capped at the current
    year before deriving anything from them.
    """
    years = [c for c in (rec.get("counts_by_year") or [])
             if c.get("works_count", 0) > 0
             and isinstance(c.get("year"), int)
             and 1900 <= c["year"] <= this_year]
    years.sort(key=lambda c: c["year"], reverse=True)

    roles = {r.get("role"): r.get("works_count")
             for r in (rec.get("roles") or []) if r.get("role")}
    ids = rec.get("ids") or {}
    geo = rec.get("geo") or {}

    return {
        "name": rec.get("display_name"),
        "alternate_names": "; ".join(rec.get("alternate_titles") or [])[:500],
        "openalex_id": (rec.get("id") or "").rsplit("/", 1)[-1],
        "ror": (ids.get("ror") or "").rsplit("/", 1)[-1] or None,
        "type": rec.get("type"),
        "country": rec.get("country_code"),
        "city": geo.get("city"),
        "region": geo.get("region"),
        "homepage": rec.get("homepage_url"),
        "is_funder": "funder" in roles,
        "is_institution": "institution" in roles,
        "works_funded": roles.get("funder"),
        "works_published": roles.get("institution"),
        "awards_count": rec.get("awards_count"),
        "works_count": rec.get("works_count"),
        "cited_by_count": rec.get("cited_by_count"),
        "first_active_year": years[-1]["year"] if years else None,
        "last_active_year": years[0]["year"] if years else None,
        "works_last_year": years[0]["works_count"] if years else 0,
        "works_last_3yr": sum(c["works_count"] for c in years
                              if c["year"] >= this_year - 3),
        "works_last_10yr": sum(c["works_count"] for c in years
                               if c["year"] >= this_year - 10),
        "active_years": len(years),
        "description": (rec.get("description") or "")[:300],
    }


COLUMNS = ["name", "alternate_names", "openalex_id", "ror", "type", "country",
           "city", "region", "homepage", "is_funder", "is_institution",
           "works_funded", "works_published", "awards_count", "works_count",
           "cited_by_count", "first_active_year", "last_active_year",
           "works_last_year", "works_last_3yr", "works_last_10yr",
           "active_years", "description"]


# ----------------------------------------------------------------------
# Excel output
# ----------------------------------------------------------------------

def write_excel(path: Path, sheets: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for sheet_name, df in sheets.items():
                df.to_excel(writer, index=False, sheet_name=sheet_name)
                ws = writer.sheets[sheet_name]
                for cell in ws[1]:
                    cell.fill = HEADER_FILL
                    cell.font = HEADER_FONT
                    cell.alignment = Alignment(vertical="center", wrap_text=True)
                widths = {"A": 44, "B": 40, "C": 14, "D": 12, "E": 14, "F": 9,
                          "G": 18, "H": 8, "I": 34, "J": 10, "K": 13,
                          "L": 13, "M": 15, "N": 13, "O": 12, "P": 14,
                          "Q": 15, "R": 15, "S": 14, "T": 14, "U": 15,
                          "V": 12, "W": 60}
                for col, w in widths.items():
                    ws.column_dimensions[col].width = w
                ws.freeze_panes = "B2"
    except PermissionError as e:
        raise PermissionError(
            f"Could not write {path.name} - is it open in Excel? "
            "Close it and re-run (the raw data is cached, so it is fast)."
        ) from e


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def load_or_fetch(entity: str, filters: str, api_key, refresh: bool) -> list:
    """Fetch from the API, or reuse the raw cache from a previous run."""
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = SOURCE_DIR / f"openalex_raw_{entity}.json"

    if raw_path.exists() and not refresh:
        recs = json.loads(raw_path.read_text(encoding="utf-8"))
        print(f"  using cached raw file ({len(recs):,} records) - "
              f"pass --refresh to refetch")
        return recs

    recs = fetch_all(entity, filters, api_key)
    raw_path.write_text(json.dumps(recs), encoding="utf-8")
    print(f"  raw records cached -> {raw_path.name}")
    return recs


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fetch US funders + company institutions from OpenAlex.")
    ap.add_argument("--funders-only", action="store_true")
    ap.add_argument("--institutions-only", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="Ignore the raw cache and refetch from the API.")
    ap.add_argument("--all-institution-types", action="store_true",
                    help="Fetch all US institutions, not just type:company "
                         "(much larger - universities, hospitals, etc).")
    args = ap.parse_args()

    # API key from .env or environment (optional)
    api_key = None
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:
        pass
    import os
    api_key = (os.getenv("OPENALEX_API_KEY") or "").strip() or None

    print("\n=== OpenAlex US fetch ===\n")
    if api_key:
        print("Using API key (daily budget $1).")
    else:
        print("No OPENALEX_API_KEY found - running keyless (budget $0.10/day,\n"
              "  only 100 search calls). Get a free key at\n"
              "  openalex.org/settings/api and add it to .env as\n"
              "  OPENALEX_API_KEY=...\n")

    this_year = date.today().year
    sheets = {}
    t0 = time.time()

    if not args.institutions_only:
        print("Fetching US funders...")
        recs = load_or_fetch("funders", "country_code:US", api_key, args.refresh)
        df = pd.DataFrame([summarise(r, this_year) for r in recs],
                          columns=COLUMNS)
        df = df.sort_values("works_funded", ascending=False, na_position="last")
        sheets["Funders"] = df
        print()

    if not args.funders_only:
        inst_filter = ("country_code:US" if args.all_institution_types
                       else "country_code:US,type:company")
        label = ("Institutions (all types)" if args.all_institution_types
                 else "Company Institutions")
        print(f"Fetching US institutions [{inst_filter}]...")
        recs = load_or_fetch("institutions", inst_filter, api_key, args.refresh)
        df = pd.DataFrame([summarise(r, this_year) for r in recs],
                          columns=COLUMNS)
        df = df.sort_values("works_published", ascending=False,
                            na_position="last")
        sheets[label] = df
        print()

    # ---- summary sheet ----
    summary_rows = []
    for name, df in sheets.items():
        summary_rows.append({
            "sheet": name,
            "records": len(df),
            "active in last 3 years": int((df["works_last_3yr"] > 0).sum()),
            "active in last 10 years": int((df["works_last_10yr"] > 0).sum()),
            "also has funder role": int(df["is_funder"].sum()),
            "also has institution role": int(df["is_institution"].sum()),
            "median works (last 3yr)": float(df["works_last_3yr"].median()),
        })
    sheets["Summary"] = pd.DataFrame(summary_rows)

    write_excel(OUT_XLSX, sheets)

    print("=" * 62)
    for r in summary_rows:
        print(f"  {r['sheet']:<26} {r['records']:>7,} records  "
              f"({r['active in last 3 years']:,} active in last 3 yrs)")
    print(f"\n  Excel written: {OUT_XLSX}")
    print(f"  Raw JSON saved in: {SOURCE_DIR}")
    print(f"  Elapsed: {time.time()-t0:.0f}s")
    print("=" * 62 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
