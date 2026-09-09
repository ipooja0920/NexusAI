#!/usr/bin/env python3
"""Funding enrichment from SBIR/STTR awards -> data/cache/funding_sbir.json

SBIR and STTR are the federal small-business R&D grant programmes. An award
is money the US government paid a company to do research, which makes it
evidence of two different things at once:

  * funding      - a dated dollar amount from a federal contract
  * credibility  - an agency vetted the science well enough to pay for it,
                   and for STTR the company had to bring a university partner

Why this complements SEC Form D rather than duplicating it: grant money is
not a securities offering, so a company living on SBIR awards files no
Form D at all. Measured against the current datasource, only 57 companies
matched both, and SBIR reached 235 companies with no Capital IQ revenue
that Form D had missed.

Two things this script is careful about, both learned from the data:

1. RECENCY IS NOT THE LIFETIME TOTAL. Inotek Pharmaceuticals shows
   $38.4m of awards - all of it between 1997 and 2005. Immunetics shows
   $37.3m, also long finished. Summing a company's whole history and
   calling it funding capacity is wrong: that money was spent decades ago.
   So the lifetime total is kept for reference, and a 5-year window is what
   represents current capacity. A window rather than the single most recent
   year, because awards are lumpy and multi-year - Quiver Bioscience booked
   $862k in 2025 but $13.9m across five years, and the single-year figure
   would understate them by 16x.

2. A COMPANY THAT MOVED IS STILL THE SAME COMPANY. SBIR stamps the address
   as at the time of the award and never revisits it, so a 1998 award
   carries a 1998 address. Matching therefore corroborates on state OR
   website and never rejects on address alone - see nexus/matching.py.

Input:  data/sources/sbir/award_data.csv
        One CSV covering every award since 1983, from
        https://www.sbir.gov/awards ("Download all SBIR.gov award data ...
        with award abstracts"). Refreshed monthly; replace the whole file.
Output: data/cache/funding_sbir.json         (keyed by normalised company name)
        data/review/funding_sbir_review.xlsx (for checking matches by eye)

Usage:
    python enrich_sbir.py
    python enrich_sbir.py --csv /path/to/award_data.csv
    python enrich_sbir.py --recent-years 3
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from nexus.matching import (TRUSTED_CONFIDENCE, match_confidence,
                            normalise_company_name, state_code,
                            state_from_address, website_domain)
from nexus.settings import Settings

# The Abstract column runs to several thousand characters, past csv's
# default field limit.
csv.field_size_limit(10 ** 9)

DEFAULT_RECENT_YEARS = 5

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)

# Placeholders that sit in the RI Name column where a research institution
# should be. "Stub" alone accounts for 15,222 of the 36,693 non-empty
# values - 41% - so counting them as university partners nearly doubles the
# apparent number of companies with academic ties.
RI_PLACEHOLDERS = {"stub", "missing", "n/a", "na", "none", "null", "-", "--",
                   "not applicable", "tbd", "unknown", ".", "no", "n a"}

# Which industries an agency's money implies. Used to tell which part of a
# multi-industry company an award relates to. Today every company in the
# datasource is classified "Pharmaceuticals, Biotechnology and Life
# Sciences", because the Capital IQ export was screened to that industry -
# so this cannot be tuned yet. It is built now so it is ready when the
# datasource spans chemicals, materials and tech.
AGENCY_INDUSTRY = {
    "department of health and human services": "Life Sciences; Health Care",
    "national institutes of health": "Life Sciences; Health Care",
    "department of defense": "Defense; Electronics; Materials",
    "national aeronautics and space administration": "Aerospace; Materials",
    "department of energy": "Energy; Chemicals; Materials",
    "national science foundation": "Research; Multiple",
    "department of agriculture": "Agriculture; Food",
    "environmental protection agency": "Environmental; Chemicals",
    "department of commerce": "Commerce; Technology",
    "department of transportation": "Transportation",
    "department of education": "Education",
    "department of homeland security": "Defense; Security",
}


# ----------------------------------------------------------------------
# Field parsing
# ----------------------------------------------------------------------

def _money(value) -> Optional[float]:
    try:
        v = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _year(value) -> Optional[int]:
    """Award Year is 100% filled; Proposal Award Date only 51.5%, and the
    two agree 99.9% of the time, so the year is the field to trust."""
    try:
        y = int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return None
    # award dates run slightly into the future because contracts are dated
    # when they start; anything wilder is junk (the file contains a 5025)
    return y if 1980 <= y <= date.today().year + 2 else None


def _date(value) -> Optional[date]:
    s = (str(value) if value is not None else "").strip()
    if not s or s.lower() in ("n/a", "none", "nan"):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"):
        try:
            d = datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
        return d if 1980 <= d.year <= date.today().year + 20 else None
    return None


def _institution(value) -> Optional[str]:
    """A real research-institution name, or None for a placeholder."""
    s = (str(value) if value is not None else "").strip()
    if not s or s.lower() in RI_PLACEHOLDERS:
        return None
    return s


def canonical_institution(name: str) -> str:
    """Fold spelling variants of the same institution together.

    SBIR writes both 'BOSTON UNIVERSITY' and 'Boston University'; 94 raw
    names in the matched set collapse to 84 on case alone. OpenAlex will
    add its own spellings later, so this lives in one place.
    """
    s = " ".join(str(name).split())
    if s.isupper() or s.islower():
        s = s.title()
        # title() mangles the small words universities are full of
        for word, fixed in (("Of", "of"), ("The", "the"), ("And", "and"),
                            ("At", "at"), ("For", "for"), ("In", "in")):
            s = s.replace(f" {word} ", f" {fixed} ")
    return s.strip(" ,.;")


# ----------------------------------------------------------------------
# Roll the award file up to one record per company
# ----------------------------------------------------------------------

def _blank_company() -> dict:
    return {
        "names": Counter(), "states": Counter(), "domains": Counter(),
        "cities": Counter(), "zips": Counter(),
        "by_year": defaultdict(float), "awards": 0,
        "phase1": 0, "phase2": 0, "sttr": 0,
        "agencies": Counter(), "institutions": Counter(),
        "inst_years": defaultdict(list), "inst_end": {},
        "employees": None, "employees_year": -1,
        "last_title": None, "last_title_year": -1,
    }


def aggregate_awards(csv_path: Path) -> tuple:
    """Stream the award file into one record per normalised company name.

    Streamed rather than loaded into a DataFrame: the with-abstracts file
    is ~385MB and only eleven of its forty-two columns are needed.
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"SBIR award file not found: {csv_path}\n"
            "Download it from https://www.sbir.gov/awards - the line reading\n"
            '  "Download all SBIR.gov award data either with award abstracts"\n'
            f"and save it as {csv_path}"
        )

    acc = defaultdict(_blank_company)
    rows = skipped = 0
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            key = normalise_company_name(row.get("Company"))
            if not key:
                skipped += 1
                continue
            a = acc[key]
            a["awards"] += 1

            name = (row.get("Company") or "").strip()
            if name:
                a["names"][name] += 1
            st = state_code(row.get("State"))
            if st:
                a["states"][st] += 1
            dom = website_domain(row.get("Company Website"))
            if dom:
                a["domains"][dom] += 1
            city = (row.get("City") or "").strip()
            if city:
                a["cities"][city] += 1
            zc = (row.get("Zip") or "").strip()[:5]
            if zc.isdigit():
                a["zips"][zc] += 1

            year = _year(row.get("Award Year"))
            amount = _money(row.get("Award Amount"))
            if year and amount:
                a["by_year"][year] += amount

            phase = (row.get("Phase") or "").strip().lower()
            if "ii" in phase:
                a["phase2"] += 1
            elif "i" in phase:
                a["phase1"] += 1
            if (row.get("Program") or "").strip().upper() == "STTR":
                a["sttr"] += 1

            agency = (row.get("Agency") or "").strip()
            if agency:
                a["agencies"][agency] += 1

            # STTR requires a research-institution partner. This is the
            # clearest evidence in any source that a company already works
            # with universities.
            inst = _institution(row.get("RI Name"))
            if inst:
                canon = canonical_institution(inst)
                a["institutions"][canon] += 1
                if year:
                    a["inst_years"][canon].append(year)
                end = _date(row.get("Contract End Date"))
                if end and (canon not in a["inst_end"] or end > a["inst_end"][canon]):
                    a["inst_end"][canon] = end

            # keep the newest value of fields that describe the company now
            emp = (row.get("Number Employees") or "").strip()
            if emp.isdigit() and year and year >= a["employees_year"]:
                a["employees"], a["employees_year"] = int(emp), year
            title = (row.get("Award Title") or "").strip()
            if title and year and year >= a["last_title_year"]:
                a["last_title"], a["last_title_year"] = title, year

    return acc, rows, skipped


def summarise(a: dict, recent_years: int) -> dict:
    """Turn one accumulator into the fields we publish."""
    today = date.today()
    cutoff = today.year - recent_years
    by_year = dict(a["by_year"])

    total = sum(by_year.values())
    recent = sum(v for y, v in by_year.items() if y >= cutoff)
    years = sorted(by_year)

    insts = [n for n, _ in a["institutions"].most_common()]
    current, past = [], []
    latest_end = None
    uni_last_year = None
    for name in insts:
        past.append(name)
        yrs = a["inst_years"].get(name) or []
        if yrs:
            uni_last_year = max(uni_last_year or 0, max(yrs))
        end = a["inst_end"].get(name)
        if end:
            if latest_end is None or end > latest_end:
                latest_end = end
            if end >= today:
                current.append(name)

    agencies = a["agencies"].most_common()
    industries = []
    for ag, _ in agencies:
        mapped = AGENCY_INDUSTRY.get(ag.lower())
        if mapped:
            for part in mapped.split(";"):
                part = part.strip()
                if part and part not in industries:
                    industries.append(part)

    return {
        "sbir_matched_name": (a["names"].most_common(1)[0][0]
                              if a["names"] else None),
        "sbir_recent_usd": round(recent, 2),
        "sbir_total_usd": round(total, 2),
        "sbir_by_year": "; ".join(
            f"{y}:${by_year[y]/1e6:.2f}M" for y in sorted(by_year, reverse=True)),
        "sbir_awards": a["awards"],
        "sbir_first_year": years[0] if years else None,
        "sbir_last_year": years[-1] if years else None,
        "sbir_phase1_awards": a["phase1"],
        "sbir_phase2_awards": a["phase2"],
        "sbir_sttr_awards": a["sttr"],
        "sbir_agencies": "; ".join(f"{n} ({c})" for n, c in agencies[:5]),
        "sbir_agency_industries": "; ".join(industries),
        "university_current": "; ".join(current),
        "university_past": "; ".join(past),
        "sbir_university_partners": len(past),
        "sbir_university_last_year": uni_last_year,
        "sbir_university_end_date": latest_end.isoformat() if latest_end else None,
        "sbir_employees": a["employees"],
        "sbir_state": (a["states"].most_common(1)[0][0] if a["states"] else None),
        "sbir_city": (a["cities"].most_common(1)[0][0] if a["cities"] else None),
        "sbir_website": (a["domains"].most_common(1)[0][0] if a["domains"] else None),
        "sbir_last_award_title": a["last_title"],
    }


# ----------------------------------------------------------------------
# Match against the company datasource
# ----------------------------------------------------------------------

def match_companies(companies: pd.DataFrame, acc: dict,
                    recent_years: int) -> list:
    rows = []
    for _, c in companies.iterrows():
        name = c.get("company")
        key = normalise_company_name(name)
        a = acc.get(key)
        if a is None:
            continue

        our_state = state_from_address(c.get("address_cleaned") or c.get("address"))
        our_domain = website_domain(c.get("website"))
        their_states = set(a["states"])
        their_domains = set(a["domains"])

        conf = match_confidence(key, our_state, their_states,
                                our_domain, their_domains)

        row = {"company": name, "confidence": conf,
               "our_state": our_state,
               "sbir_states": "; ".join(sorted(their_states)),
               "our_website": our_domain}
        row.update(summarise(a, recent_years))
        row["ciq_revenue_usdmm"] = c.get("revenue_usdmm")
        row["ciq_employees"] = c.get("employees")
        row["key"] = key
        rows.append(row)
    return rows


# ----------------------------------------------------------------------
# Review workbook
# ----------------------------------------------------------------------

REVIEW_COLUMNS = [
    "company", "sbir_matched_name", "confidence", "our_state", "sbir_states",
    "our_website", "sbir_website",
    "sbir_recent_usd", "sbir_total_usd", "sbir_by_year",
    "sbir_awards", "sbir_first_year", "sbir_last_year",
    "sbir_phase1_awards", "sbir_phase2_awards", "sbir_sttr_awards",
    "sbir_agencies", "sbir_agency_industries",
    "university_current", "university_past", "sbir_university_partners",
    "sbir_university_last_year", "sbir_university_end_date",
    "sbir_employees", "ciq_employees", "ciq_revenue_usdmm",
    "sbir_city", "sbir_last_award_title", "key",
]


def write_review(path: Path, matches: list, stats: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    m = pd.DataFrame(matches)
    if not m.empty:
        m = m.reindex(columns=[c for c in REVIEW_COLUMNS if c in m.columns])
        # suspect first: those are the rows a human most needs to look at
        order = {"suspect": 0, "unverified": 1, "high": 2}
        m = m.assign(_o=m["confidence"].map(order).fillna(9)) \
             .sort_values(["_o", "sbir_total_usd"], ascending=[True, False]) \
             .drop(columns="_o")
    s = pd.DataFrame([{"metric": k, "value": v} for k, v in stats.items()])
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            m.to_excel(w, index=False, sheet_name="Matches")
            s.to_excel(w, index=False, sheet_name="Summary")
            for sheet in w.sheets.values():
                for cell in sheet[1]:
                    cell.fill = HEADER_FILL
                    cell.font = HEADER_FONT
                    cell.alignment = Alignment(vertical="center", wrap_text=True)
                sheet.freeze_panes = "B2"
            ws = w.sheets["Matches"]
            for col, width in (("A", 34), ("B", 34), ("C", 12), ("J", 46),
                               ("Q", 34), ("S", 34), ("T", 46)):
                ws.column_dimensions[col].width = width
    except PermissionError as e:
        raise PermissionError(
            f"Could not write {path.name} - is it open in Excel? Close it and "
            "re-run (parsing takes seconds, nothing is lost)."
        ) from e


# ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Enrich companies with SBIR/STTR federal R&D awards.")
    ap.add_argument("--csv", help="Path to award_data.csv.")
    ap.add_argument("--recent-years", type=int, default=DEFAULT_RECENT_YEARS,
                    help=f"Window for sbir_recent_usd "
                         f"(default {DEFAULT_RECENT_YEARS}).")
    args = ap.parse_args()

    cfg = Settings()
    csv_path = Path(args.csv) if args.csv else \
        (cfg.sources_dir / "sbir" / "award_data.csv")
    cache_path = cfg.cache_dir / "funding_sbir.json"
    review_path = cfg.review_dir / "funding_sbir_review.xlsx"

    print("\n=== NexusAI: SBIR/STTR award enrichment ===\n")
    print(f"Reading awards from: {csv_path}")
    acc, rows, skipped = aggregate_awards(csv_path)
    print(f"  {rows:,} awards read")
    if skipped:
        print(f"  {skipped:,} skipped (no usable company name)")
    print(f"  {len(acc):,} distinct companies in the SBIR index")

    try:
        from nexus.excel_io import read_enriched
        companies = read_enriched(cfg.companies_enriched)
        src = "companies_enriched.xlsx"
    except FileNotFoundError:
        from nexus.excel_io import read_capitaliq
        companies = read_capitaliq(cfg.companies_raw, cfg.capitaliq)
        src = "companies_raw.xlsx"
    print(f"\n  matching against {len(companies):,} companies from {src}")

    matches = match_companies(companies, acc, args.recent_years)
    good = [m for m in matches if m["confidence"] in TRUSTED_CONFIDENCE]

    cache = {m["key"]: {k: v for k, v in m.items() if k != "key"} for m in good}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1, default=str),
                          encoding="utf-8")

    by_conf = Counter(m["confidence"] for m in matches)
    no_rev = sum(1 for m in good if pd.isna(m.get("ciq_revenue_usdmm")))
    with_recent = sum(1 for m in good if (m.get("sbir_recent_usd") or 0) > 0)
    with_uni = sum(1 for m in good if m.get("sbir_university_partners"))
    with_cur = sum(1 for m in good if m.get("university_current"))

    stats = {
        "companies in datasource": len(companies),
        "SBIR companies indexed": len(acc),
        "name matches (all)": len(matches),
        **{f"  confidence: {k}": v for k, v in sorted(by_conf.items())},
        "kept in cache (high + unverified)": len(good),
        "matched with NO Capital IQ revenue": no_rev,
        f"with an award in the last {args.recent_years} years": with_recent,
        "with a university research partner": with_uni,
        "with a CURRENT university partner": with_cur,
        "recent funding across kept matches (USD)":
            round(sum(m.get("sbir_recent_usd") or 0 for m in good), 2),
        "lifetime awards across kept matches (USD)":
            round(sum(m.get("sbir_total_usd") or 0 for m in good), 2),
    }
    write_review(review_path, matches, stats)

    print("\n" + "=" * 64)
    for k, v in stats.items():
        print(f"  {k:<48}{v:>14,}" if isinstance(v, (int, float))
              else f"  {k:<48}{v:>14}")
    print(f"\n  cache written : {cache_path}")
    print(f"  review sheet  : {review_path}")
    print("=" * 64 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
