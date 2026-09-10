#!/usr/bin/env python3
"""R&D expense from SEC XBRL  ->  data/cache/rd_xbrl.json

Capital IQ reports how big a company is - revenue, headcount - but not how
much of that it spends on research. For a faculty partnership that is the
more relevant number: a company putting hundreds of millions a year into
R&D is a research organisation whatever its revenue looks like, and some of
the most research-intensive companies in the datasource report almost no
revenue at all.

This is the ONLY source that does not use nexus/matching.py, because it
does not need to. Capital IQ names public companies with their exchange
ticker, SEC publishes a ticker-to-CIK map, and the XBRL frames are keyed by
CIK. So the join is an exact identifier lookup end to end:

    Capital IQ "Exchange:Ticker" column  ->  ACET
    SEC company_tickers.json             ->  CIK 1720580
    XBRL frame                           ->  R&D expense

No name comparison, no address check, no confidence grading - a ticker
belongs to exactly one company. That is the strongest matching available,
and it is possible only because both sides carry a registered identifier,
which is exactly what private companies lack. The trade-off is reach: this
covers only publicly listed companies.

Input:  data/sources/xbrl/  - written by fetch_sec_xbrl.py
Output: data/cache/rd_xbrl.json      (keyed by normalised company name, so
                                      the join in nexus/funding.py works the
                                      same way as every other source)
        data/review/rd_xbrl_review.xlsx

Usage:
    python enrich_xbrl.py
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from nexus.matching import normalise_company_name
from nexus.scoring import parse_employees, parse_revenue
from nexus.settings import Settings

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def load_ticker_map(source_dir: Path) -> dict:
    """ticker -> CIK, from SEC's published map."""
    path = source_dir / "company_tickers.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Ticker map not found: {path}\n"
            "Run 'python fetch_sec_xbrl.py' first - it downloads this."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for row in data.values():
        try:
            out[str(row["ticker"]).upper()] = int(row["cik_str"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def load_rd(source_dir: Path) -> tuple:
    """CIK -> {year: rd_expense}. Reads every year fetch_sec_xbrl.py wrote."""
    files = sorted(source_dir.glob("rd_expense_CY*.csv"))
    if not files:
        raise FileNotFoundError(
            f"No R&D CSVs in {source_dir}\n"
            "Run 'python fetch_sec_xbrl.py' first."
        )
    out: dict = {}
    for path in files:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    cik = int(row["cik"])
                    year = int(row["fiscal_year"])
                    val = float(row["rd_expense_usd"])
                except (KeyError, TypeError, ValueError):
                    continue
                if val > 0:
                    out.setdefault(cik, {})[year] = val
    return out, [p.name for p in files]


def ticker_of(value) -> Optional[str]:
    """Capital IQ writes 'NasdaqCM:ACET' or '-'. Return the bare symbol."""
    s = (str(value) if value is not None else "").strip()
    if not s or s == "-" or s.lower() == "nan":
        return None
    return (s.split(":")[-1] if ":" in s else s).strip().upper() or None


def match_companies(companies: pd.DataFrame, tickers: dict, rd: dict) -> list:
    rows = []
    for _, c in companies.iterrows():
        tick = ticker_of(c.get("ticker"))
        if not tick:
            continue                       # private company - nothing to match
        cik = tickers.get(tick)
        if cik is None:
            continue                       # foreign listing, or delisted
        years = rd.get(cik)
        if not years:
            continue                       # public but reports no R&D

        latest = max(years)
        rows.append({
            "company": c.get("company"),
            "ticker": tick,
            "cik": cik,
            "rd_expense_usd": round(years[latest], 2),
            "rd_expense_year": latest,
            "rd_by_year": "; ".join(f"{y}:${years[y]/1e6:.2f}M"
                                    for y in sorted(years, reverse=True)),
            "rd_years_reported": len(years),
            # read_capitaliq gives the RAW strings from the sheet, so these
            # need parsing - the enriched workbook's numeric columns do not
            # exist yet at this point in the pipeline.
            "ciq_revenue_usdmm": parse_revenue(c.get("revenue")),
            "ciq_employees": parse_employees(c.get("employees")) or None,
            "key": normalise_company_name(c.get("company")),
        })
    return rows


REVIEW_COLUMNS = ["company", "ticker", "cik", "rd_expense_usd",
                  "rd_expense_year", "rd_by_year", "rd_years_reported",
                  "ciq_revenue_usdmm", "ciq_employees", "key"]


def write_review(path: Path, matches: list, stats: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    m = pd.DataFrame(matches)
    if not m.empty:
        m = m.reindex(columns=REVIEW_COLUMNS)
        m = m.sort_values("rd_expense_usd", ascending=False)
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
            w.sheets["Matches"].column_dimensions["A"].width = 40
            w.sheets["Matches"].column_dimensions["F"].width = 46
    except PermissionError as e:
        raise PermissionError(
            f"Could not write {path.name} - is it open in Excel? Close it and "
            "re-run (this reads local files only, so it costs nothing)."
        ) from e


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Match SEC XBRL R&D expense to the company datasource.")
    ap.add_argument("--sources", help="Folder holding the fetched XBRL files.")
    args = ap.parse_args()

    cfg = Settings()
    source_dir = Path(args.sources) if args.sources else (cfg.sources_dir / "xbrl")
    cache_path = cfg.cache_dir / "rd_xbrl.json"
    review_path = cfg.review_dir / "rd_xbrl_review.xlsx"

    print("\n=== NexusAI: SEC XBRL R&D expense ===\n")
    print(f"Reading from: {source_dir}")
    tickers = load_ticker_map(source_dir)
    rd, files = load_rd(source_dir)
    print(f"  ticker map      : {len(tickers):,} tickers")
    print(f"  R&D frames      : {', '.join(files)}")
    print(f"  companies with R&D reported: {len(rd):,}")

    from nexus.excel_io import read_capitaliq
    companies = read_capitaliq(cfg.companies_raw, cfg.capitaliq)
    print(f"\n  matching against {len(companies):,} companies")

    with_ticker = sum(1 for _, c in companies.iterrows()
                      if ticker_of(c.get("ticker")))
    matches = match_companies(companies, tickers, rd)

    cache = {m["key"]: {k: v for k, v in m.items() if k != "key"}
             for m in matches if m["key"]}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1, default=str),
                          encoding="utf-8")

    no_rev = sum(1 for m in matches if not m.get("ciq_revenue_usdmm"))
    stats = {
        "companies in datasource": len(companies),
        "carrying a ticker": with_ticker,
        "ticker resolved to a CIK": sum(
            1 for _, c in companies.iterrows()
            if (t := ticker_of(c.get("ticker"))) and t in tickers),
        "matched with R&D reported": len(matches),
        "  of those, no Capital IQ revenue": no_rev,
        "total R&D across matches (USD)":
            round(sum(m["rd_expense_usd"] for m in matches), 2),
    }
    write_review(review_path, matches, stats)

    print("\n" + "=" * 62)
    for k, v in stats.items():
        print(f"  {k:<40}{v:>18,}" if isinstance(v, (int, float))
              else f"  {k:<40}{v:>18}")
    print(f"\n  cache written : {cache_path}")
    print(f"  review sheet  : {review_path}")
    print("=" * 62 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
