#!/usr/bin/env python3
"""Every raw Form D filing behind our companies  ->  data/review/formd_raw.csv

The enriched workbook gives one aggregated line per company. This gives the
evidence underneath it: one row per FILING, with the Form D columns under
their own SEC names, so a figure in the enriched sheet can be traced back to
the filings that produced it.

It deliberately applies NO cleaning:

  * pooled investment funds are NOT dropped
  * amendments are NOT collapsed
  * no min-year cut

...because the point is to see what SEC actually published before the
pipeline forms an opinion about it. Two helper columns say what the pipeline
WOULD do, without doing it:

  _would_be_dropped   why enrich_funding.py would discard this row, if it would
  _span_years         years between first sale and filing - the gap that makes
                      bucketing TOTALAMOUNTSOLD by filing year wrong

The only thing it adds is the join: which of our companies each filing
belongs to, using the same name rule as enrich_funding.py so the rows here
are the same rows that feed the enriched sheet.

Input:  data/sources/formd/  (quarterly datasets, .zip or unzipped)
Output: data/review/formd_raw.csv   - one row per filing

One raw CSV per funding source, kept out of the enriched workbook: the
workbook is the aggregated answer, these are the evidence behind it.

Usage:
    python export_formd_raw.py
    python export_formd_raw.py --all          # every filing, not just ours
    python export_formd_raw.py --company Agilix
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from enrich_funding import _money, _parse_date, load_quarter
from nexus.matching import (match_confidence, normalise_company_name,
                            state_code, state_from_address)
from nexus.settings import Settings

FUND_TYPE = "Pooled Investment Fund"

# Raw Form D columns kept, under their own names. Not every column - the
# three tables carry 80-odd between them, most of them signature blocks and
# exemption checkboxes - but every column that bears on the money or the
# dates, which is what these rows are for.
ISSUER_COLS = ["ENTITYNAME", "CIK", "STREET1", "CITY", "STATEORCOUNTRY",
               "ZIPCODE", "ENTITYTYPE", "JURISDICTIONOFINC",
               "YEAROFINC_VALUE_ENTERED"]
OFFER_COLS = ["INDUSTRYGROUPTYPE", "REVENUERANGE", "ISAMENDMENT",
              "PREVIOUSACCESSIONNUMBER", "SALE_DATE", "YETTOOCCUR",
              "MORETHANONEYEAR", "TOTALOFFERINGAMOUNT", "TOTALAMOUNTSOLD",
              "TOTALREMAINING", "HASNONACCREDITEDINVESTORS",
              "TOTALNUMBERALREADYINVESTED"]
SUB_COLS = ["FILING_DATE", "SUBMISSIONTYPE"]

#: Our columns come first so the sheet reads left to right as
#: "our company -> what SEC says about it".
OURS = ["company", "formd_key", "match_confidence", "our_state"]
HELPERS = ["_would_be_dropped", "_span_years", "_quarter", "ACCESSIONNUMBER"]


def load_raw(formd_dir: Path) -> pd.DataFrame:
    """Every filing from every quarter, uncleaned, columns kept as named."""
    if not formd_dir.exists():
        raise FileNotFoundError(
            f"Form D folder not found: {formd_dir}\n"
            "Download quarterly datasets from\n"
            "  https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"
        )
    sources = sorted([p for p in formd_dir.iterdir()
                      if (p.is_file() and p.suffix.lower() == ".zip") or p.is_dir()])
    if not sources:
        raise FileNotFoundError(f"No .zip files or folders inside {formd_dir}")

    frames = []
    for src in sources:
        tabs = load_quarter(src)
        if tabs is None:
            print(f"  [skip] {src.name} - no Form D tables inside")
            continue
        iss, off, sub = (tabs["ISSUERS.tsv"], tabs["OFFERING.tsv"],
                         tabs["FORMDSUBMISSION.tsv"])

        def keep(df, cols):
            return df[["ACCESSIONNUMBER"] + [c for c in cols if c in df.columns]]

        df = (keep(iss, ISSUER_COLS)
              .merge(keep(off, OFFER_COLS), on="ACCESSIONNUMBER", how="inner")
              .merge(keep(sub, SUB_COLS), on="ACCESSIONNUMBER", how="left"))
        df["_quarter"] = src.name
        frames.append(df)
        print(f"  loaded {src.name:<22} {len(df):>7,} filings")

    if not frames:
        raise RuntimeError("No usable Form D quarters were loaded.")
    return pd.concat(frames, ignore_index=True)


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    """Add the two helper columns, without removing any row."""
    filed = df["FILING_DATE"].map(_parse_date)
    sale = df["SALE_DATE"].map(_parse_date) if "SALE_DATE" in df.columns \
        else pd.Series([None] * len(df))

    # The gap that makes filing-year bucketing wrong. Blank when either date
    # is missing - an unknown span must not read as a zero-length one.
    df["_span_years"] = [
        None if (f is None or s is None) else round((f - s).days / 365.25, 1)
        for f, s in zip(filed, sale)
    ]

    # What the pipeline would do with this row. Stated, not done.
    superseded = set(
        df.loc[df["PREVIOUSACCESSIONNUMBER"].notna(), "PREVIOUSACCESSIONNUMBER"]
        .astype(str).str.strip()
    )
    reasons = []
    for _, r in df.iterrows():
        why = []
        if r.get("INDUSTRYGROUPTYPE") == FUND_TYPE:
            why.append("pooled investment fund")
        if str(r.get("ACCESSIONNUMBER", "")).strip() in superseded:
            why.append("superseded by an amendment")
        if _parse_date(r.get("FILING_DATE")) is None:
            why.append("unparseable filing date")
        reasons.append("; ".join(why))
    df["_would_be_dropped"] = reasons
    return df


def attach_companies(df: pd.DataFrame, companies: pd.DataFrame,
                     keep_all: bool) -> pd.DataFrame:
    """Join our company list on, using the same name rule as the pipeline."""
    df["formd_key"] = df["ENTITYNAME"].map(normalise_company_name)

    ours = {}
    for _, c in companies.iterrows():
        key = normalise_company_name(c.get("company"))
        if key:
            ours[key] = (c.get("company"),
                         state_from_address(c.get("address_cleaned")
                                            or c.get("address")))

    names, confs, states = [], [], []
    for key, their_state in zip(df["formd_key"], df.get("STATEORCOUNTRY", [])):
        hit = ours.get(key)
        if hit is None:
            names.append(None); confs.append(None); states.append(None)
            continue
        name, our_state = hit
        names.append(name)
        states.append(our_state)
        confs.append(match_confidence(key, our_state, state_code(their_state)))
    df["company"], df["match_confidence"], df["our_state"] = names, confs, states

    if not keep_all:
        df = df[df["company"].notna()]
    return df


def write_csv(path: Path, df: pd.DataFrame) -> None:
    """One flat CSV. No workbook, no sheets, no formatting.

    A raw extract is something to grep, diff and load into pandas, not
    something to read by eye - and the enriched WORKBOOK is the one artefact
    that should be a workbook. Each funding source gets its own CSV here so
    they can be regenerated and compared independently.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = (OURS + HELPERS
            + [c for c in ISSUER_COLS if c in df.columns]
            + [c for c in OFFER_COLS if c in df.columns]
            + [c for c in SUB_COLS if c in df.columns])
    out = df.reindex(columns=[c for c in cols if c in df.columns])
    try:
        out.to_csv(path, index=False, encoding="utf-8")
    except PermissionError as e:
        raise PermissionError(
            f"Could not write {path.name} - is it open in Excel? Close it and "
            "re-run (this reads local files only, so it costs nothing)."
        ) from e


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export raw Form D filings joined to our companies.")
    ap.add_argument("--formd-dir", help="Folder holding the quarterly datasets.")
    ap.add_argument("--out", help="Where to write the CSV.")
    ap.add_argument("--all", action="store_true",
                    help="Keep every filing, not just ones matching a company.")
    ap.add_argument("--company",
                    help="Keep only companies whose name contains this.")
    args = ap.parse_args()

    cfg = Settings()
    formd_dir = Path(args.formd_dir) if args.formd_dir else (cfg.sources_dir / "formd")
    out_path = Path(args.out) if args.out else (cfg.review_dir / "formd_raw.csv")

    print("\n=== NexusAI: raw Form D export ===\n")
    raw = load_raw(formd_dir)
    print(f"\n  {len(raw):,} filings loaded, uncleaned")

    raw = annotate(raw)

    from nexus.excel_io import read_capitaliq
    companies = read_capitaliq(cfg.companies_raw, cfg.capitaliq)
    print(f"  matching against {len(companies):,} companies")

    df = attach_companies(raw, companies, keep_all=args.all)
    if args.company:
        df = df[df["company"].astype(str)
                  .str.contains(args.company, case=False, na=False)]

    amounts = [a for a in df["TOTALAMOUNTSOLD"].map(_money) if a]
    # Note the `s == s` test: assigning None into a DataFrame column turns it
    # into NaN, and NaN is not None, so `is not None` lets every blank span
    # through and max() then returns nan. Same NaN trap as nexus/funding.py.
    spans = [s for s in df["_span_years"] if s is not None and s == s]
    stats = {
        "filings in export": len(df),
        "distinct companies": df["company"].nunique(),
        "rows the pipeline would drop":
            int((df["_would_be_dropped"].astype(str) != "").sum()),
        "  superseded by an amendment": int(df["_would_be_dropped"]
            .astype(str).str.contains("superseded").sum()),
        "  pooled investment funds": int(df["_would_be_dropped"]
            .astype(str).str.contains("pooled").sum()),
        "rows with no SALE_DATE": int(df["SALE_DATE"].isna().sum())
            if "SALE_DATE" in df.columns else 0,
        "offerings spanning >1 year": sum(1 for s in spans if s > 1),
        "longest span (years)": max(spans) if spans else 0,
        "sum of TOTALAMOUNTSOLD as filed": round(sum(amounts), 2),
    }
    write_csv(out_path, df)

    print("\n" + "=" * 62)
    for k, v in stats.items():
        print(f"  {k:<40}{v:>18,}" if isinstance(v, (int, float))
              else f"  {k:<40}{v:>18}")
    print(f"\n  written : {out_path}")
    print("=" * 62 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
