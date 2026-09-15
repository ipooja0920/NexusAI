#!/usr/bin/env python3
"""Fetch US research funders and research-publishing companies from OpenAlex.

Downloads only. Matching to our own company list is a separate step, the
same way SEC XBRL is fetched and then matched.

OpenAlex answers two different questions about a company, and keeps them in
two different indexes:

    funders       did this company PAY for academic research?
    institutions  did this company PUBLISH research of its own?

Those are separate records with separate ids (F... and I...) even when they
describe the same organisation - NIH's funder record points at a different
id than its institution record. The two are linked through each record's
`roles` array, which is how one company's funding and publishing history
end up on the same row here.

Why both matter: a company that pays outside researchers is the closest
thing in any dataset to "would they fund a UConn project". A company that
publishes has its own scientists, so it would understand the work and its
papers show what it actually does.

Output: data/sources/openalex/openalex_us.xlsx
        data/sources/openalex/openalex_raw_funders.json       (cached)
        data/sources/openalex/openalex_raw_institutions.json  (cached)

Columns:
    company             OpenAlex display name
    funded_research     works this company paid others to produce
    grants_funded       number of grants awarded
    funding_history     "2025:12; 2024:8; 2023:15"
    funder_website      homepage on the FUNDER record
    published_research  works this company's own staff produced
    publishing_history  "2025:40; 2024:35"
    publisher_website   homepage on the INSTITUTION record
    openalex_ids        the F.../I... ids behind the row

Every year is kept, with no cutoff. The history columns make an inactive
company visible as inactive rather than making it disappear, which is the
same choice made for SBIR and Form D - filtering later is easy, but
un-filtering means refetching.

Usage:
    python fetch_openalex.py
    python fetch_openalex.py --funders-only
    python fetch_openalex.py --all-institution-types   # not just companies
    python fetch_openalex.py --refresh                 # ignore the cache
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
SOURCE_DIR = PROJECT_ROOT / "data" / "sources" / "openalex"
OUT_XLSX = SOURCE_DIR / "openalex_us.xlsx"

API = "https://api.openalex.org"
PER_PAGE = 100                  # documented maximum
CONTACT = "lakshmipoojaraj@gmail.com"
PAUSE = 0.15                    # well inside the documented 100 req/sec

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)


# ----------------------------------------------------------------------
# HTTP (stdlib only - no requests dependency)
# ----------------------------------------------------------------------

def _get_json(url: str, retries: int = 4) -> dict:
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": f"NexusAI-UConn ({CONTACT})",
                              "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:                       # noqa: BLE001
            last = e
            code = getattr(e, "code", None)
            if code in (400, 401, 403):
                raise
            wait = 2 ** attempt
            print(f"    [retry {attempt}/{retries}] {e} - waiting {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"giving up after {retries} attempts: {last}")


def fetch_all(entity: str, filters: str, api_key: Optional[str]) -> list:
    """Cursor-paginate an OpenAlex endpoint and return every record."""
    records, cursor, page_no = [], "*", 0
    while cursor:
        params = {"filter": filters, "per-page": str(PER_PAGE),
                  "cursor": cursor, "mailto": CONTACT}
        if api_key:
            params["api_key"] = api_key
        data = _get_json(f"{API}/{entity}?" + urllib.parse.urlencode(params))

        batch = data.get("results", [])
        records.extend(batch)
        page_no += 1
        meta = data.get("meta", {})
        total = meta.get("count")
        if page_no == 1:
            print(f"  {total:,} {entity} match the filter")
        if page_no % 20 == 0:
            print(f"    {len(records):,}/{total:,} fetched")
        cursor = meta.get("next_cursor")
        if not batch:
            break
        time.sleep(PAUSE)
    print(f"  done: {len(records):,} records")
    return records


def load_or_fetch(entity: str, filters: str, api_key, refresh: bool) -> list:
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = SOURCE_DIR / f"openalex_raw_{entity}.json"
    if raw_path.exists() and not refresh:
        recs = json.loads(raw_path.read_text(encoding="utf-8"))
        print(f"  using cached {raw_path.name} ({len(recs):,} records) - "
              f"pass --refresh to refetch")
        return recs
    recs = fetch_all(entity, filters, api_key)
    raw_path.write_text(json.dumps(recs), encoding="utf-8")
    print(f"  cached -> {raw_path.name}")
    return recs


# ----------------------------------------------------------------------
# Shaping
# ----------------------------------------------------------------------

def _short_id(value) -> Optional[str]:
    """'https://openalex.org/F4320306076' -> 'F4320306076'."""
    if not value:
        return None
    return str(value).rsplit("/", 1)[-1] or None


def history(rec: dict, this_year: int) -> tuple:
    """(count, "2025:12; 2024:8", first_year, last_year) from counts_by_year.

    counts_by_year carries junk future years in the source data - the NSF
    record has an entry for 2604 - so years are capped at the current one.
    """
    years = {c["year"]: c["works_count"]
             for c in (rec.get("counts_by_year") or [])
             if isinstance(c.get("year"), int)
             and 1900 <= c["year"] <= this_year
             and (c.get("works_count") or 0) > 0}
    if not years:
        return None, None, None, None
    text = "; ".join(f"{y}:{years[y]}" for y in sorted(years, reverse=True))
    return sum(years.values()), text, min(years), max(years)


def role_ids(rec: dict) -> dict:
    """{'funder': 'F123', 'institution': 'I456'} for one record.

    This is the link between the two indexes: a company that both funds and
    publishes has one record in each, and each record's roles array names
    the other's id.
    """
    out = {}
    for r in (rec.get("roles") or []):
        role, rid = r.get("role"), _short_id(r.get("id"))
        if role and rid:
            out[role] = rid
    return out


def build_rows(funders: list, institutions: list, this_year: int) -> list:
    """One row per organisation, merging its funder and institution records."""
    inst_by_id = {_short_id(r.get("id")): r for r in institutions}

    rows, used_institutions = [], set()

    def row_for(funder: Optional[dict], inst: Optional[dict]) -> dict:
        f_count = f_hist = f_first = f_last = None
        i_count = i_hist = i_first = i_last = None
        if funder:
            f_count, f_hist, f_first, f_last = history(funder, this_year)
        if inst:
            i_count, i_hist, i_first, i_last = history(inst, this_year)
        primary = funder or inst
        ids = [i for i in (_short_id((funder or {}).get("id")),
                           _short_id((inst or {}).get("id"))) if i]
        return {
            "company": primary.get("display_name"),
            "funded_research": f_count,
            "grants_funded": (funder or {}).get("grants_count") or None,
            "funding_history": f_hist,
            "funder_website": (funder or {}).get("homepage_url") or None,
            "published_research": i_count,
            "publishing_history": i_hist,
            "publisher_website": (inst or {}).get("homepage_url") or None,
            "first_year": min([y for y in (f_first, i_first) if y], default=None),
            "last_year": max([y for y in (f_last, i_last) if y], default=None),
            "country": primary.get("country_code"),
            "openalex_ids": "; ".join(ids),
        }

    for f in funders:
        inst_id = role_ids(f).get("institution")
        inst = inst_by_id.get(inst_id) if inst_id else None
        if inst is not None:
            used_institutions.add(_short_id(inst.get("id")))
        rows.append(row_for(f, inst))

    # institutions with no funder record of their own - companies that
    # publish research but have never funded anyone else's
    for i in institutions:
        if _short_id(i.get("id")) not in used_institutions:
            rows.append(row_for(None, i))

    return rows


COLUMNS = ["company", "funded_research", "grants_funded", "funding_history",
           "funder_website", "published_research", "publishing_history",
           "publisher_website", "first_year", "last_year", "country",
           "openalex_ids"]


def write_excel(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="OpenAlex US")
            ws = w.sheets["OpenAlex US"]
            for cell in ws[1]:
                cell.fill = HEADER_FILL
                cell.font = HEADER_FONT
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            for col, width in (("A", 42), ("B", 15), ("C", 13), ("D", 40),
                               ("E", 30), ("F", 17), ("G", 40), ("H", 30),
                               ("I", 10), ("J", 10), ("K", 9), ("L", 26)):
                ws.column_dimensions[col].width = width
            ws.freeze_panes = "B2"
    except PermissionError as e:
        raise PermissionError(
            f"Could not write {path.name} - is it open in Excel? Close it and "
            "re-run (the raw JSON is cached, so it costs nothing)."
        ) from e


# ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fetch US research funders and publishing companies.")
    ap.add_argument("--funders-only", action="store_true")
    ap.add_argument("--institutions-only", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="Ignore the cached raw JSON and refetch.")
    ap.add_argument("--all-institution-types", action="store_true",
                    help="Fetch every US institution, not just type:company "
                         "(much larger - universities, hospitals and more).")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:                                # noqa: BLE001
        pass
    import os
    api_key = (os.getenv("OPENALEX_API_KEY") or "").strip() or None

    print("\n=== NexusAI: OpenAlex US fetch ===\n")
    print("Using an API key (daily budget $1)." if api_key else
          "No OPENALEX_API_KEY found - running keyless (budget $0.10/day).\n"
          "  Get one free at openalex.org/settings/api and add it to .env as\n"
          "  OPENALEX_API_KEY=...\n")

    this_year = date.today().year
    funders, institutions = [], []
    t0 = time.time()

    if not args.institutions_only:
        print("Funders - companies that PAID for research:")
        funders = load_or_fetch("funders", "country_code:US", api_key,
                                args.refresh)
        print()

    if not args.funders_only:
        inst_filter = ("country_code:US" if args.all_institution_types
                       else "country_code:US,type:company")
        print(f"Institutions - companies that PUBLISHED research "
              f"[{inst_filter}]:")
        institutions = load_or_fetch("institutions", inst_filter, api_key,
                                     args.refresh)
        print()

    rows = build_rows(funders, institutions, this_year)
    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.sort_values(["funded_research", "published_research"],
                        ascending=False, na_position="last")
    write_excel(OUT_XLSX, df)

    both = int((df["funded_research"].notna() & df["published_research"].notna()).sum())
    print("=" * 64)
    print(f"  organisations                    {len(df):>10,}")
    print(f"    funded research only           {int((df['funded_research'].notna() & df['published_research'].isna()).sum()):>10,}")
    print(f"    published research only        {int((df['funded_research'].isna() & df['published_research'].notna()).sum()):>10,}")
    print(f"    did both                       {both:>10,}")
    print(f"  with a funder website            {int(df['funder_website'].notna().sum()):>10,}")
    print(f"  with a publisher website         {int(df['publisher_website'].notna().sum()):>10,}")
    print(f"\n  Excel written : {OUT_XLSX}")
    print(f"  Raw JSON      : {SOURCE_DIR}")
    print(f"  Elapsed       : {time.time()-t0:.0f}s")
    print("=" * 64 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
