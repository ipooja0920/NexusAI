#!/usr/bin/env python3
"""Funding enrichment from SEC Form D  ->  data/cache/funding_formd.json

Form D is the notice a company files when it raises private capital
(seed/Series A/B, convertible notes) without registering with the SEC. It
gives a dated dollar amount straight from the company's own filing - hard
evidence rather than an AI guess - and it covers precisely the young private
companies Capital IQ has no revenue for.

This is a SEPARATE script from enrich_companies.py on purpose:
  * funding goes stale (a raise from 2021 is not news); geocodes never do
  * you refresh funding quarterly without re-geocoding, and refresh the
    datasource without re-reading Form D
  * enrich_companies.py rebuilds the enriched file from scratch, so funding
    lives in a cache that survives that rebuild

Form D is one funding source among several (SBIR is next). Each source gets
its own reader, its own cache file and its own review sheet, so they refresh
independently and a bug in one never forces a rebuild of the others.

Input:  quarterly Form D datasets in data/sources/formd/ - either the .zip
        files as downloaded, or unzipped folders. Get them from
        https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets
Output: data/cache/funding_formd.json         (keyed by normalised company name)
        data/review/funding_formd_review.xlsx (for checking the matches by eye)

Usage:
    python enrich_funding.py
    python enrich_funding.py --formd-dir /path/to/quarters
    python enrich_funding.py --min-year 2023      # ignore older filings
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from nexus.matching import (TRUSTED_CONFIDENCE, match_confidence,
                            normalise_company_name, state_code,
                            state_from_address)
from nexus.settings import Settings

NEEDED = ["ISSUERS.tsv", "OFFERING.tsv", "FORMDSUBMISSION.tsv"]
FUND_TYPE = "Pooled Investment Fund"     # VC/PE/hedge funds, not operating cos
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)


# ----------------------------------------------------------------------
# Reading the quarterly datasets
# ----------------------------------------------------------------------

def _read_tsv(buf) -> pd.DataFrame:
    return pd.read_csv(buf, sep="\t", dtype=str, on_bad_lines="skip",
                       encoding="utf-8", encoding_errors="replace")


def load_quarter(source: Path) -> Optional[dict]:
    """Read one quarter from a .zip or an unzipped folder. Returns the three
    tables we need, or None if this source doesn't contain them."""
    tables = {}
    if source.is_file() and source.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source) as z:
                names = {Path(n).name.upper(): n for n in z.namelist()}
                for want in NEEDED:
                    hit = names.get(want.upper())
                    if hit is None:
                        return None
                    with z.open(hit) as fh:
                        tables[want] = _read_tsv(io.BytesIO(fh.read()))
        except zipfile.BadZipFile:
            print(f"    [skip] {source.name} is not a valid zip")
            return None
    elif source.is_dir():
        found = {p.name.upper(): p for p in source.rglob("*.tsv")}
        for want in NEEDED:
            hit = found.get(want.upper())
            if hit is None:
                return None
            tables[want] = _read_tsv(hit)
    else:
        return None
    return tables


def load_all_formd(formd_dir: Path) -> pd.DataFrame:
    """Read every quarter found under formd_dir into one filings table."""
    if not formd_dir.exists():
        raise FileNotFoundError(
            f"Form D folder not found: {formd_dir}\n"
            "Download quarterly datasets from\n"
            "  https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets\n"
            f"and put the .zip files in {formd_dir}\n"
            "(one .zip or one unzipped folder per quarter - both work)"
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
        iss, off, sub = tabs["ISSUERS.tsv"], tabs["OFFERING.tsv"], tabs["FORMDSUBMISSION.tsv"]

        keep_off = ["ACCESSIONNUMBER", "INDUSTRYGROUPTYPE", "TOTALAMOUNTSOLD",
                    "TOTALOFFERINGAMOUNT", "REVENUERANGE", "ISAMENDMENT",
                    "PREVIOUSACCESSIONNUMBER"]
        keep_sub = ["ACCESSIONNUMBER", "FILING_DATE", "SUBMISSIONTYPE"]
        off = off[[c for c in keep_off if c in off.columns]]
        sub = sub[[c for c in keep_sub if c in sub.columns]]

        df = (iss.merge(off, on="ACCESSIONNUMBER", how="inner")
                 .merge(sub, on="ACCESSIONNUMBER", how="left"))
        df["_source"] = src.name
        frames.append(df)
        print(f"  loaded {src.name:<22} {len(df):>7,} filings")

    if not frames:
        raise RuntimeError("No usable Form D quarters were loaded.")
    return pd.concat(frames, ignore_index=True)


# ----------------------------------------------------------------------
# Cleaning
# ----------------------------------------------------------------------

def _money(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("$", "")
    if s in ("", "-", "nan", "None"):
        return None
    try:
        f = float(s)
        return f if f >= 0 else None
    except ValueError:
        return None


def _parse_date(v) -> Optional[date]:
    """Form D dates look like '15-APR-2026'."""
    if not v or str(v).strip() in ("", "nan"):
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None


def prepare(df: pd.DataFrame, min_year: Optional[int]) -> pd.DataFrame:
    """Drop funds, parse money/dates, resolve amendments, keep operating cos."""
    n0 = len(df)
    df = df[df["INDUSTRYGROUPTYPE"] != FUND_TYPE].copy()
    print(f"\n  dropped {n0-len(df):,} pooled-investment-fund filings "
          f"({len(df):,} operating-company filings remain)")

    df["amount"] = df["TOTALAMOUNTSOLD"].map(_money)
    df["offering_amount"] = df["TOTALOFFERINGAMOUNT"].map(_money)
    df["filed"] = df["FILING_DATE"].map(_parse_date)
    df = df[df["filed"].notna()]

    if min_year:
        before = len(df)
        df = df[df["filed"].map(lambda d: d.year) >= min_year]
        print(f"  dropped {before-len(df):,} filings before {min_year}")

    # Amendments (D/A) restate an earlier notice for the SAME offering.
    # Summing them would multiply one raise into several, so we keep only the
    # latest filing in each amendment chain.
    superseded = set(
        df.loc[df["PREVIOUSACCESSIONNUMBER"].notna(), "PREVIOUSACCESSIONNUMBER"]
        .astype(str).str.strip()
    )
    before = len(df)
    df = df[~df["ACCESSIONNUMBER"].astype(str).str.strip().isin(superseded)]
    print(f"  dropped {before-len(df):,} filings superseded by an amendment")

    df["key"] = df["ENTITYNAME"].map(normalise_company_name)
    df = df[df["key"] != ""]
    return df


# ----------------------------------------------------------------------
# Aggregate per company
# ----------------------------------------------------------------------

def aggregate(df: pd.DataFrame) -> dict:
    """One record per normalised company name."""
    out = {}
    for key, g in df.groupby("key", sort=False):
        g = g.sort_values("filed")
        amounts = [a for a in g["amount"] if a is not None and a > 0]
        latest = g.iloc[-1]
        rev_ranges = [r for r in g["REVENUERANGE"]
                      if r and str(r).strip() not in
                      ("", "nan", "Decline to Disclose", "Not Applicable")]

        # Money raised per calendar year. A single lifetime total hides the
        # difference between a company still raising and one that stopped
        # years ago, and this is the column that lets that be checked by eye
        # rather than taken on trust. Mirrors sbir_by_year.
        by_year = {}
        for _, r in g.iterrows():
            amt = _money(r["amount"])
            if amt:
                y = r["filed"].year
                by_year[y] = by_year.get(y, 0.0) + amt

        out[key] = {
            "entity_name": latest["ENTITYNAME"],
            "cik": latest.get("CIK"),
            "state": latest.get("STATEORCOUNTRY"),
            "city": latest.get("CITY"),
            "filings": int(len(g)),
            "total_raised_usd": float(sum(amounts)) if amounts else None,
            "last_raise_usd": _money(latest["amount"]),
            "last_raise_date": latest["filed"].isoformat(),
            "last_raise_year": latest["filed"].year,
            "largest_raise_usd": float(max(amounts)) if amounts else None,
            "by_year": "; ".join(f"{y}:${by_year[y]/1e6:.2f}M"
                                 for y in sorted(by_year, reverse=True)),
            "industry_group": latest.get("INDUSTRYGROUPTYPE"),
            "revenue_range": rev_ranges[-1] if rev_ranges else None,
            "accession": latest.get("ACCESSIONNUMBER"),
        }
    return out


# ----------------------------------------------------------------------
# Match against the company datasource
# ----------------------------------------------------------------------

def match_companies(companies: pd.DataFrame, formd: dict) -> list:
    """Join our companies to the Form D index.

    Uses the same rule as every other source - nexus.matching.match_confidence
    - rather than its own. A differing state used to reject a match here,
    which discarded companies for the one thing that carries no information:
    a filing records the address as at the time of filing, and companies
    move. Alexion is in MA and its older records say CT.

    There is no website fallback, unlike SBIR: Form D's ISSUERS table has no
    website column, so there is no domain to fall back to.

    There is deliberately no PREVIOUS-NAME fallback either, though the table
    offers six such columns and they would add 22 matches. Those columns
    record legal continuity, not business continuity - most of the 22 are
    reverse mergers, where a shell company takes on an entirely different
    business. Six of them have BOTH names in the datasource (Arsanis and
    X4 Pharmaceuticals; AVROBIO and Tectonic Therapeutic), so matching on
    the previous name would attribute one company's raise to two. A domain
    tracks the business and is safe to follow; a previous name does not.
    """
    rows = []
    for _, c in companies.iterrows():
        name = c.get("company")
        key = normalise_company_name(name)
        rec = formd.get(key)
        if rec is None:
            continue

        our_state = state_from_address(c.get("address_cleaned") or c.get("address"))
        their_state = state_code(rec.get("state"))
        confidence = match_confidence(key, our_state, their_state)

        rows.append({
            "company": name,
            "matched_entity": rec["entity_name"],
            "confidence": confidence,
            "our_state": our_state,
            "formd_state": their_state,
            "filings": rec["filings"],
            "last_raise_usd": rec["last_raise_usd"],
            "last_raise_date": rec["last_raise_date"],
            "last_raise_year": rec["last_raise_year"],
            "total_raised_usd": rec["total_raised_usd"],
            "by_year": rec["by_year"],
            "largest_raise_usd": rec["largest_raise_usd"],
            "revenue_range": rec["revenue_range"],
            "industry_group": rec["industry_group"],
            "ciq_revenue_usdmm": c.get("revenue_usdmm"),
            "ciq_employees": c.get("employees"),
            "cik": rec["cik"],
            "accession": rec["accession"],
            "key": key,
        })
    return rows


def write_review(path: Path, matches: list, stats: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    m = pd.DataFrame(matches)
    if not m.empty:
        m = m.sort_values("last_raise_usd", ascending=False, na_position="last")
    s = pd.DataFrame([{"metric": k, "value": v} for k, v in stats.items()])
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            (m if not m.empty else pd.DataFrame({"note": ["no matches"]})
             ).to_excel(w, index=False, sheet_name="Matches")
            s.to_excel(w, index=False, sheet_name="Summary")
            for sheet in w.sheets.values():
                for cell in sheet[1]:
                    cell.fill = HEADER_FILL
                    cell.font = HEADER_FONT
                    cell.alignment = Alignment(vertical="center", wrap_text=True)
                sheet.freeze_panes = "B2"
            if not m.empty:
                ws = w.sheets["Matches"]
                for col, wid in {"A": 40, "B": 40, "C": 18, "D": 10, "E": 12,
                                 "F": 9, "G": 16, "H": 15, "I": 16, "J": 16,
                                 "K": 24, "L": 24, "M": 16, "N": 14,
                                 "O": 12, "P": 22, "Q": 30}.items():
                    ws.column_dimensions[col].width = wid
    except PermissionError as e:
        raise PermissionError(
            f"Could not write {path.name} - is it open in Excel? Close it "
            "and re-run (Form D parsing is fast, nothing is lost)."
        ) from e


# ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Enrich companies with SEC Form D funding.")
    ap.add_argument("--formd-dir", help="Folder holding quarterly Form D zips/folders.")
    ap.add_argument("--min-year", type=int, default=None,
                    help="Ignore filings before this year.")
    args = ap.parse_args()

    cfg = Settings()
    formd_dir = Path(args.formd_dir) if args.formd_dir else \
        (cfg.sources_dir / "formd")
    cache_path = cfg.cache_dir / "funding_formd.json"
    review_path = cfg.review_dir / "funding_formd_review.xlsx"

    print("\n=== NexusAI: Form D funding enrichment ===\n")
    print(f"Reading Form D quarters from: {formd_dir}")
    raw = load_all_formd(formd_dir)
    print(f"\n  {len(raw):,} total filings across all quarters")

    df = prepare(raw, args.min_year)
    formd = aggregate(df)
    print(f"  {len(formd):,} distinct companies in the Form D index")

    # our companies: prefer the enriched file (has cleaned addresses)
    try:
        from nexus.excel_io import read_enriched
        companies = read_enriched(cfg.companies_enriched)
        src = "companies_enriched.xlsx"
    except FileNotFoundError:
        from nexus.excel_io import read_capitaliq
        companies = read_capitaliq(cfg.companies_raw, cfg.capitaliq)
        src = "companies_raw.xlsx"
    print(f"\n  matching against {len(companies):,} companies from {src}")

    matches = match_companies(companies, formd)
    good = [m for m in matches if m["confidence"] in TRUSTED_CONFIDENCE]

    # cache keyed by normalised name, only trustworthy matches
    cache = {m["key"]: {k: v for k, v in m.items() if k != "key"} for m in good}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")

    filled = [m for m in good if pd.isna(m["ciq_revenue_usdmm"])]
    stats = {
        "companies in datasource": len(companies),
        "Form D companies indexed": len(formd),
        "matches (all)": len(matches),
        "matches kept": len(good),
        "matches rejected (suspect)": len(matches) - len(good),
        "matched companies with NO Capital IQ revenue": len(filled),
        "total raised across kept matches (USD)":
            float(sum(m["last_raise_usd"] or 0 for m in good)),
    }
    write_review(review_path, matches, stats)

    print("\n" + "=" * 62)
    for k, v in stats.items():
        print(f"  {k:<48} {v:,.0f}" if isinstance(v, float) else f"  {k:<48} {v}")
    print(f"\n  cache written : {cache_path}")
    print(f"  review sheet  : {review_path}")
    print("=" * 62 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
