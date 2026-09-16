#!/usr/bin/env python3
"""Dump the raw Form D filings behind one company  ->  stdout

The enriched workbook gives one line per company. When a figure there looks
wrong, the question is always the same: which filings produced it? This
prints them, unaggregated, so the arithmetic can be checked by eye.

It applies NONE of the cleaning that enrich_funding.py does - no fund drop,
no amendment resolution, no min-year cut - on purpose. The point is to see
what the raw data says before the pipeline forms an opinion about it.

Usage:
    python inspect_formd.py "Agilix"
    python inspect_formd.py "Agilix" --formd-dir /path/to/quarters
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from enrich_funding import _money, _parse_date, load_quarter
from nexus.settings import Settings

SHOW = ["filed", "SUBMISSIONTYPE", "amount", "offering_amount",
        "ISAMENDMENT", "ACCESSIONNUMBER", "PREVIOUSACCESSIONNUMBER",
        "ENTITYNAME", "CITY", "STATEORCOUNTRY", "INDUSTRYGROUPTYPE"]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Show every raw Form D filing matching a name.")
    ap.add_argument("name", help="Substring of the entity name, case-insensitive.")
    ap.add_argument("--formd-dir", help="Folder holding the quarterly datasets.")
    args = ap.parse_args()

    cfg = Settings()
    formd_dir = Path(args.formd_dir) if args.formd_dir else (cfg.sources_dir / "formd")

    sources = sorted([p for p in formd_dir.iterdir()
                      if (p.is_file() and p.suffix.lower() == ".zip") or p.is_dir()])
    if not sources:
        print(f"No Form D quarters found in {formd_dir}")
        return 1

    hits = []
    for src in sources:
        tabs = load_quarter(src)
        if tabs is None:
            continue
        iss, off, sub = (tabs["ISSUERS.tsv"], tabs["OFFERING.tsv"],
                         tabs["FORMDSUBMISSION.tsv"])
        m = iss[iss["ENTITYNAME"].astype(str)
                   .str.contains(args.name, case=False, na=False)]
        if m.empty:
            continue
        df = (m.merge(off, on="ACCESSIONNUMBER", how="left")
               .merge(sub, on="ACCESSIONNUMBER", how="left"))
        df["_quarter"] = src.name
        hits.append(df)

    if not hits:
        print(f"No Form D filing mentions '{args.name}'.")
        return 0

    df = pd.concat(hits, ignore_index=True)
    df["amount"] = df["TOTALAMOUNTSOLD"].map(_money)
    df["offering_amount"] = df["TOTALOFFERINGAMOUNT"].map(_money)
    df["filed"] = df["FILING_DATE"].map(_parse_date)
    df = df.sort_values("filed")

    cols = [c for c in SHOW if c in df.columns]
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 40)
    print(f"\n{len(df)} filing(s) matching '{args.name}':\n")
    print(df[cols].to_string(index=False))

    amounts = [a for a in df["amount"] if a]
    print(f"\n  sum of TOTALAMOUNTSOLD as filed : ${sum(amounts):,.0f}")
    print(f"  largest single filing           : ${max(amounts):,.0f}"
          if amounts else "  no amounts reported")
    print(f"  most recent filing              : ${amounts[-1]:,.0f}"
          if amounts else "")

    # The question that decides whether summing is right: do these filings
    # link to each other? An unlinked amendment chain restates one offering
    # cumulatively, and summing it multiplies a single raise into several.
    linked = df["PREVIOUSACCESSIONNUMBER"].notna().sum() \
        if "PREVIOUSACCESSIONNUMBER" in df.columns else 0
    amend = (df["ISAMENDMENT"].astype(str).str.upper() == "Y").sum() \
        if "ISAMENDMENT" in df.columns else 0
    print(f"\n  flagged as amendments           : {amend}")
    print(f"  carrying a PREVIOUSACCESSIONNUMBER: {linked}")
    if amend and not linked:
        print("\n  WARNING: amendments that do not name the filing they amend."
              "\n  These are NOT dropped by enrich_funding.py, so if they "
              "restate\n  the same offering the total above is inflated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
