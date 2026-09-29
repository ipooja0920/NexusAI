#!/usr/bin/env python3
"""SBIR/STTR awards, cleaned and rolled up per company  ->  data/cache/sbir_clean.csv

One row per COMPANY. No Capital IQ here - this only turns the raw download
into something clean enough to match against later.

What it does:

  1. Cleans every award row: money to a number, dates to dates, zip to five
     digits, the "Stub" research-institution placeholder blanked out.

  2. Marks each award ACTIVE if its Contract End Date is today or later.
     After that date nothing new can be charged to the award, so it is the
     direct test of "money available now" - no guessing with a year window.

  3. Rolls the awards up per company:
       sbir_active_usd       sum of active awards
       sbir_active_until     latest end date among active awards
       sbir_total_usd        sum of every award, for reference
       university_current    research institutions on ACTIVE awards
       university_past       research institutions on ENDED awards only
       award_titles          most recent titles   } for enriching the company
       abstracts             most recent abstracts } description later

Companies are grouped by UEI, the government's unique entity ID. UEI is
blank on older awards (it replaced DUNS in 2022), so those rows borrow the
UEI of the same company name from its newer awards. Without that, one
company's old and new awards would split into two rows.

"Active" depends on the day this runs. Re-run it whenever award_data.csv
is refreshed, and treat the printed "as of" date as part of the answer.

Input:  data/sources/sbir/award_data.csv   (https://www.sbir.gov/awards)
Output: data/cache/sbir_clean.csv

Usage:
    python clean_sbir.py
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from nexus.matching import normalise_company_name

# The columns read from the raw file. The other 28 - solicitation and
# tracking numbers, ownership flags, and the contact / PI names, emails and
# phones - are not read at all. The personal data in particular has no use
# here and should not travel any further than the raw download.
RAW_COLUMNS = [
    "Company", "UEI", "Award Title", "Abstract", "Award Amount",
    "Award Year", "Contract End Date", "Number Employees",
    "Company Website", "Address1", "Address2", "City", "State", "Zip",
    "RI Name",
]

# Placeholders that mean "no research institution". "Stub" alone is on
# 15,222 rows.
NO_INSTITUTION = {"", "stub", "n/a", "na", "none", "nan", "-"}

# Enough recent titles and abstracts to describe what a company works on,
# without one busy company producing a cell too large for Excel to open
# (limit 32,767 characters - one company has 1,974 awards).
MAX_TITLES = 10
MAX_ABSTRACTS = 3

OUT_COLUMNS = [
    "company", "uei", "company_website", "address", "city", "state", "zip",
    "number_employees",
    "sbir_active_usd", "sbir_active_until", "sbir_total_usd",
    "university_current", "university_past",
    "award_titles", "abstracts",
]


def _text(s: pd.Series) -> pd.Series:
    """Trimmed text; newlines flattened so a row stays one line in Excel."""
    return (s.fillna("").astype(str)
             .str.replace(r"\s+", " ", regex=True).str.strip())


def load_awards(path: Path) -> pd.DataFrame:
    """Part 1: read and clean every award row."""
    raw = pd.read_csv(path, dtype=str, usecols=RAW_COLUMNS,
                      keep_default_na=True, low_memory=False)

    a = pd.DataFrame({
        "company": _text(raw["Company"]),
        "uei": _text(raw["UEI"]).str.upper(),
        "award_title": _text(raw["Award Title"]),
        "abstract": _text(raw["Abstract"]),
        "award_amount": pd.to_numeric(raw["Award Amount"], errors="coerce"),
        "award_year": pd.to_numeric(raw["Award Year"], errors="coerce"),
        "contract_end_date": pd.to_datetime(raw["Contract End Date"],
                                            errors="coerce"),
        "number_employees": pd.to_numeric(raw["Number Employees"],
                                          errors="coerce"),
        "company_website": _text(raw["Company Website"]),
        "address": (_text(raw["Address1"]) + " " +
                    _text(raw["Address2"])).str.strip(),
        "city": _text(raw["City"]),
        "state": _text(raw["State"]),
        "zip": _text(raw["Zip"]).str[:5],
        "ri_name": _text(raw["RI Name"]),
    })
    a.loc[a["ri_name"].str.lower().isin(NO_INSTITUTION), "ri_name"] = ""
    a.loc[~a["zip"].str.fullmatch(r"\d{5}"), "zip"] = ""
    for col in ("company_website",):
        a.loc[a[col].str.lower().isin({"nan", "none", "n/a", "-"}), col] = ""
    return a[a["company"] != ""].copy()


def assign_company_key(a: pd.DataFrame) -> pd.Series:
    """UEI where present; otherwise the UEI of the same company name from
    its newer awards; otherwise the normalised name itself."""
    norm = a["company"].map(normalise_company_name)
    has = a["uei"] != ""
    # A name that maps to exactly one UEI can lend it to its UEI-less rows.
    # A name shared by several UEIs is ambiguous, so it is left alone.
    pairs = (pd.DataFrame({"norm": norm[has], "uei": a.loc[has, "uei"]})
               .drop_duplicates())
    counts = pairs.groupby("norm")["uei"].nunique()
    lend = pairs[pairs["norm"].isin(counts[counts == 1].index)] \
        .set_index("norm")["uei"]
    borrowed = norm.map(lend)
    return a["uei"].where(has, borrowed.fillna("NAME:" + norm))


def _join(values, limit=None) -> str:
    seen, out = set(), []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
            if limit and len(out) >= limit:
                break
    return "; ".join(out)


def roll_up(a: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """Part 2: one row per company."""
    a = a.copy()
    a["active"] = a["contract_end_date"] >= today
    # newest first, so "most recent" below is simply the first match
    a["_order"] = a["contract_end_date"].fillna(
        pd.to_datetime(a["award_year"].astype("Int64").astype(str) + "-12-31",
                       errors="coerce"))
    a = a.sort_values("_order", ascending=False, na_position="last")

    rows = []
    for key, g in a.groupby("_key", sort=False):
        act = g[g["active"]]
        first = lambda col: next((v for v in g[col] if v not in ("", None)
                                  and not (isinstance(v, float) and v != v)),
                                 "")
        current = _join(act["ri_name"])
        current_set = set(current.split("; ")) if current else set()
        past = _join(v for v in g.loc[~g["active"], "ri_name"]
                     if v not in current_set)
        emp = g["number_employees"].dropna()
        rows.append({
            "company": g["company"].iloc[0],
            "uei": "" if key.startswith("NAME:") else key,
            "company_website": first("company_website"),
            "address": first("address"),
            "city": first("city"),
            "state": first("state"),
            "zip": first("zip"),
            "number_employees": int(emp.iloc[0]) if len(emp) else None,
            "sbir_active_usd": round(act["award_amount"].sum(), 2)
                               if len(act) else None,
            "sbir_active_until": act["contract_end_date"].max().date()
                                 if len(act) else None,
            "sbir_total_usd": round(g["award_amount"].sum(), 2),
            "university_current": current,
            "university_past": past,
            "award_titles": _join(g["award_title"], MAX_TITLES),
            "abstracts": " || ".join(
                [x for x in dict.fromkeys(g["abstract"]) if x][:MAX_ABSTRACTS]),
        })
    out = pd.DataFrame(rows, columns=OUT_COLUMNS)
    return out.sort_values("sbir_active_usd", ascending=False,
                           na_position="last")


def main() -> int:
    ap = argparse.ArgumentParser(description="Clean and roll up SBIR awards.")
    ap.add_argument("--csv", help="Path to award_data.csv")
    ap.add_argument("--out", help="Where to write sbir_clean.csv")
    args = ap.parse_args()

    if args.csv and args.out:
        src, dst = Path(args.csv), Path(args.out)
    else:
        from nexus.settings import Settings
        cfg = Settings()
        src = Path(args.csv) if args.csv else cfg.sources_dir / "sbir" / "award_data.csv"
        dst = Path(args.out) if args.out else cfg.cache_dir / "sbir_clean.csv"

    today = pd.Timestamp(date.today())
    print(f"\n=== NexusAI: clean SBIR awards ===  (as of {today.date()})\n")
    print(f"  reading {src}")
    awards = load_awards(src)
    awards["_key"] = assign_company_key(awards)
    print(f"  awards                 {len(awards):>9,}")

    companies = roll_up(awards, today)

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        companies.to_csv(dst, index=False, encoding="utf-8")
    except PermissionError as e:
        raise PermissionError(f"Could not write {dst.name} - is it open in "
                              "Excel? Close it and re-run.") from e

    act = companies["sbir_active_usd"].notna()
    print(f"  companies              {len(companies):>9,}")
    print(f"    with an active award {act.sum():>9,}")
    print(f"    university current   {(companies['university_current'] != '').sum():>9,}")
    print(f"    university past only {((companies['university_past'] != '') & (companies['university_current'] == '')).sum():>9,}")
    print(f"  active funding total   ${companies['sbir_active_usd'].sum()/1e9:>8,.2f}bn")
    print(f"\n  written: {dst}")
    print(f"  'active' means contract end date on or after {today.date()}.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
