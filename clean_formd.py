#!/usr/bin/env python3
"""SEC Form D filings, cleaned  ->  data/cache/formd_clean.csv

One row per FILING by an operating company. No Capital IQ here, and no
rolling up per company - that happens at the combine step.

How Form D works, because every column below follows from it:

  * A company files Form D within 15 days of the FIRST SALE in a private
    fundraise (an "offering"), then files AMENDMENTS (D/A) while the
    offering stays open - at least yearly.
  * Each filing reports the TOTAL sold in that offering SO FAR, not the new
    money since the last filing. So an offering filed three times reports
    a running total three times:
        2023  $187,000   ->  2024  $6,908,457   ->  2025  $10,023,457
  * So the NEW money a filing adds is its total minus the previous
    filing's total. That is raised_since_last_filing, and it is the column
    that says when money actually came in.

Removed entirely:
  * pooled investment funds (VC / PE / hedge funds raising from their own
    investors) - 120,216 of 196,602 filings, not operating companies
  * co-issuers: a filing can list extra companies, and each would
    otherwise be credited the full amount. Only the primary issuer is kept.

Kept but FLAGGED, so they can be excluded at the combine step:
  * is_business_combination - the filing relates to a merger or
    acquisition, not an ordinary raise
  * first_sale_yet_to_occur - announced, nothing sold yet

Personal data is never read: the RELATEDPERSONS and SIGNATURES tables (names
of executives and signers) are not opened at all.

Input:  data/sources/formd/<quarter>/   (.zip or unzipped folders)
        https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets
Output: data/cache/formd_clean.csv

Usage:
    python clean_formd.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from enrich_funding import load_quarter

FUND = "Pooled Investment Fund"

OUT_COLUMNS = [
    "company", "cik", "city", "state", "zip",
    "offering_id", "filing_number", "is_latest_filing",
    "filing_date", "first_sale_date", "first_sale_yet_to_occur",
    "offering_target", "total_raised_to_date", "raised_since_last_filing",
    "security_type", "number_of_investors",
    "industry_group", "revenue_range", "company_founded_year",
    "is_business_combination", "accession_number",
]


def _t(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()


def _true(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.lower() == "true"


def load_filings(formd_dir: Path) -> pd.DataFrame:
    """Every operating-company filing, primary issuer only, one row each."""
    frames = []
    for src in sorted(p for p in formd_dir.iterdir()
                      if p.is_dir() or p.suffix.lower() == ".zip"):
        tabs = load_quarter(src)
        if tabs is None:
            continue
        iss = tabs["ISSUERS.tsv"]
        iss = iss[iss["IS_PRIMARYISSUER_FLAG"].str.upper() == "YES"]
        frames.append(tabs["OFFERING.tsv"]
                      .merge(tabs["FORMDSUBMISSION.tsv"], on="ACCESSIONNUMBER")
                      .merge(iss, on="ACCESSIONNUMBER"))
    d = pd.concat(frames, ignore_index=True)
    d = d[d["INDUSTRYGROUPTYPE"] != FUND]
    return d.drop_duplicates("ACCESSIONNUMBER").reset_index(drop=True)


def security_type(r) -> str:
    kinds = []
    if r["ISEQUITYTYPE"] == "true":
        kinds.append("equity")
    if r["ISDEBTTYPE"] == "true":
        kinds.append("debt")
    other = str(r.get("DESCRIPTIONOFOTHERTYPE") or "")
    if r["ISOTHERTYPE"] == "true":
        kinds.append("SAFE" if "simple agreement" in other.lower()
                     or "safe" in other.lower().split() else "other")
    if r["ISOPTIONTOACQUIRETYPE"] == "true" and not kinds:
        kinds.append("option/warrant")
    return "; ".join(kinds)


def chain(d: pd.DataFrame) -> pd.DataFrame:
    """Link each amendment to the filing it amends and work out the new
    money each filing adds."""
    acc = d["ACCESSIONNUMBER"]
    prev = d["PREVIOUSACCESSIONNUMBER"].where(d["PREVIOUSACCESSIONNUMBER"].isin(set(acc)))
    parent = dict(zip(acc, prev))

    def root(a):
        seen = set()
        while isinstance(parent.get(a), str) and a not in seen:
            seen.add(a)
            a = parent[a]
        return a

    d["offering_id"] = acc.map(root)
    amt = dict(zip(acc, d["total_raised_to_date"]))
    filed = dict(zip(acc, d["filing_date"]))
    first_sale = dict(zip(acc, pd.to_datetime(d["SALE_DATE"], errors="coerce")))

    def new_money(a, p, amended):
        if isinstance(p, str):                  # previous filing is in our data
            now, before = amt.get(a), amt.get(p)
            if pd.isna(now) or pd.isna(before):
                return None
            # A previous total of $0 AFTER the first sale had already
            # happened is impossible - money had come in, so the $0 was a
            # reporting error. Subtracting from it would credit years of
            # money to this one filing: ROM Technologies reported $0 in
            # 2025 on a raise selling since 2014, then $448m in 2026, and
            # all $448m read as 2026 money. 236 filings, $4.7bn, did this.
            # The genuine case - $0 because nothing was sold YET - is kept.
            fs, pf = first_sale.get(a), filed.get(p)
            if before == 0 and pd.notna(fs) and pd.notna(pf) and fs < pf:
                return None
            # A total can go DOWN (about 3% of amendments) when a company
            # corrects an earlier figure. That is no new money, not a refund.
            return max(0.0, now - before)
        if amended:
            # Amends a filing from before our first quarter: the total
            # includes earlier money we cannot separate out.
            return None
        return amt.get(a)                       # an original filing: all new
    d["raised_since_last_filing"] = [
        new_money(a, p, am) for a, p, am in
        zip(acc, prev, d["PREVIOUSACCESSIONNUMBER"].notna())]

    d = d.sort_values(["offering_id", "filing_date", "ACCESSIONNUMBER"])
    d["filing_number"] = d.groupby("offering_id").cumcount() + 1
    last = d.groupby("offering_id")["filing_number"].transform("max")
    d["is_latest_filing"] = d["filing_number"] == last
    return d


def clean(formd_dir: Path) -> pd.DataFrame:
    d = load_filings(formd_dir)
    d["filing_date"] = pd.to_datetime(d["FILING_DATE"], format="%d-%b-%Y",
                                      errors="coerce")
    d["total_raised_to_date"] = pd.to_numeric(d["TOTALAMOUNTSOLD"], errors="coerce")
    d = chain(d)

    out = pd.DataFrame({
        "company": _t(d["ENTITYNAME"]),
        "cik": _t(d["CIK"]).str.lstrip("0"),
        "city": _t(d["CITY"]),
        "state": _t(d["STATEORCOUNTRY"]),
        "zip": _t(d["ZIPCODE"]).str[:5],
        "offering_id": d["offering_id"],
        "filing_number": d["filing_number"],
        "is_latest_filing": d["is_latest_filing"],
        "filing_date": d["filing_date"].dt.date,
        "first_sale_date": pd.to_datetime(d["SALE_DATE"], errors="coerce").dt.date,
        "first_sale_yet_to_occur": _true(d["YETTOOCCUR"]),
        "total_raised_to_date": d["total_raised_to_date"],
        "raised_since_last_filing": d["raised_since_last_filing"],
        # "Indefinite" = no ceiling set; kept as the word, not blanked
        "offering_target": _t(d["TOTALOFFERINGAMOUNT"]),
        "security_type": d.apply(security_type, axis=1),
        "number_of_investors": pd.to_numeric(d["TOTALNUMBERALREADYINVESTED"],
                                             errors="coerce").astype("Int64"),
        "industry_group": _t(d["INDUSTRYGROUPTYPE"]),
        "revenue_range": _t(d["REVENUERANGE"]),
        "company_founded_year": pd.to_numeric(d["YEAROFINC_VALUE_ENTERED"],
                                           errors="coerce").astype("Int64"),
        "is_business_combination": _true(d["ISBUSINESSCOMBINATIONTRANS"]),
        "accession_number": d["ACCESSIONNUMBER"],
    })
    out.loc[~out["zip"].str.fullmatch(r"\d{5}"), "zip"] = ""
    out = out[out["company"] != ""]
    return out.sort_values(["company", "offering_id", "filing_number"])[OUT_COLUMNS] \
              .reset_index(drop=True)


def main() -> int:
    from nexus.settings import Settings
    cfg = Settings()
    src = cfg.sources_dir / "formd"
    dst = cfg.cache_dir / "formd_clean.csv"
    print(f"\n=== NexusAI: clean Form D filings ===\n\n  reading {src}")
    out = clean(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.to_csv(dst, index=False, encoding="utf-8")
    except PermissionError as e:
        raise PermissionError(f"Could not write {dst.name} - is it open in "
                              "Excel? Close it and re-run.") from e
    n_off = out["offering_id"].nunique()
    print(f"  filings (operating companies)   {len(out):>8,}")
    print(f"    in {n_off:,} offerings")
    print(f"    new money known               {out['raised_since_last_filing'].notna().sum():>8,}")
    print(f"    new money unknown (pre-2023)  {out['raised_since_last_filing'].isna().sum():>8,}")
    print(f"    flagged merger / acquisition  {out['is_business_combination'].sum():>8,}")
    print(f"    flagged first sale yet to occur {out['first_sale_yet_to_occur'].sum():>6,}")
    print(f"  companies (by CIK)              {out['cik'].nunique():>8,}")
    print(f"\n  written: {dst}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
