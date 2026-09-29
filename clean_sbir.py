#!/usr/bin/env python3
"""SBIR/STTR awards, cleaned  ->  data/cache/sbir_clean.csv

One row per AWARD. No Capital IQ here, and no rolling up per company -
that happens later, at the Capital IQ step, straight from these rows.

One row per award keeps each award's title, abstract, research partner and
dates together. Rolling up per company lost that: a company row listed
every partner across every active award, beside only its three newest
abstracts, so it read as if several universities worked on one abstract.

What it does to each row:

  * money to a number, dates to dates, zip to five digits
  * the "Stub" research-institution placeholder blanked out; institution
    names otherwise kept exactly as written
  * award_active_today = Contract End Date is today or later. After that
    date nothing new can be charged to the award, so it is the direct test
    of "money available now".

UEI, the government's unique company ID, is blank on older awards (it
replaced DUNS in 2022). Those rows borrow the UEI of the same company name
from its newer awards, so all of one company's rows share one UEI.

"Active" depends on the day this runs. Re-run it whenever award_data.csv
is refreshed; the "as of" date is printed.

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
    "Company", "UEI", "Agency", "Award Title", "Abstract", "Award Amount",
    "Award Year", "Contract End Date", "Number Employees",
    "Company Website", "Address1", "Address2", "City", "State", "Zip",
    "RI Name",
]

# Placeholders that mean "no research institution". "Stub" alone is on
# 15,222 rows.
NO_INSTITUTION = {"", "stub", "n/a", "na", "none", "nan", "-"}


OUT_COLUMNS = [
    "company", "uei", "agency", "award_title", "abstract", "award_amount",
    "award_year", "contract_end_date", "award_active_today", "ri_name",
    "number_employees", "company_website", "address", "city", "state", "zip",
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
        # The funding agency is the cleanest label for what AREA an award is
        # in - Energy, Health and Human Services, Defense - which matters
        # when one company works in several and is later folded into a
        # single Capital IQ row.
        "agency": _text(raw["Agency"]),
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
    # Names are otherwise kept exactly as SBIR wrote them. Recasing them
    # turned "UNH" into "Unh" and "MIT" into "Mit", and one row per award
    # has no need to reconcile spellings. That matters only when partners
    # are counted per company, which happens at the Capital IQ step.
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



def clean(src: Path, today) -> tuple:
    """The whole cleaning, minus writing and printing: (awards, company_key).

    `today` is passed in rather than read from the clock, so "active" can
    be tested against a fixed date. main() passes the real one.
    """
    today = pd.Timestamp(today)
    a = load_awards(src)
    key = assign_company_key(a)
    a["uei"] = key.where(~key.str.startswith("NAME:"), "")
    a["award_active_today"] = a["contract_end_date"] >= today
    a["contract_end_date"] = a["contract_end_date"].dt.date
    a["award_year"] = a["award_year"].astype("Int64")
    a["number_employees"] = a["number_employees"].astype("Int64")

    # a company's awards together, newest first
    a["_key"] = key
    a = a.sort_values(["_key", "contract_end_date", "award_year"],
                      ascending=[True, False, False], na_position="last")
    return a[OUT_COLUMNS].reset_index(drop=True), key


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
    out, key = clean(src, today)

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.to_csv(dst, index=False, encoding="utf-8")
    except PermissionError as e:
        raise PermissionError(f"Could not write {dst.name} - is it open in "
                              "Excel? Close it and re-run.") from e

    act = out["award_active_today"]
    print(f"  awards                     {len(out):>9,}")
    print(f"    active today             {act.sum():>9,}")
    print(f"    with a research partner  {(out['ri_name'] != '').sum():>9,}")
    print(f"    active AND a partner     {(act & (out['ri_name'] != '')).sum():>9,}")
    print(f"    no end date (older)      {out['contract_end_date'].isna().sum():>9,}")
    print(f"  companies (by UEI or name) {key.nunique():>9,}")
    print(f"\n  written: {dst}")
    print(f"  'active' means contract end date on or after {today.date()}.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
