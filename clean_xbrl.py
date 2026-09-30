#!/usr/bin/env python3
"""SEC XBRL figures, cleaned  ->  data/cache/xbrl_clean.csv

One row per COMPANY per CONCEPT per PERIOD, read from the frames that
fetch_sec_xbrl.py downloaded. No Capital IQ here - matching happens at the
combine step, exactly like clean_sbir.py and clean_formd.py.

Four concepts, each a question about a public company:

  rd_expense              what it spent on research in a calendar year
  revenue                 what it earned in a calendar year
  cash                    cash in the bank on a quarter-end
  short_term_investments  money parked in securities it can sell within a
                          year, on a quarter-end - where biotechs often keep
                          most of what they raised

The first two are FLOWS (a whole year, period CY2025). The last two are
STOCKS (one day, period CY2026Q2I = as at 30 June 2026).

Why several SEC tags per concept: each company's accountants choose which
tag to report under, and asking for the wrong one returns nothing rather
than zero. Kymera reports revenue only under the ASC 606 tag; Organogenesis
only under "Revenues". So every tag is read and, for each company and
period, the FIRST tag in the list below that has a figure is used. Tags are
alternatives for the same number - they are never added together. The
`tag` column records which one was used.

Each frame file is identified by the `tag` and `ccp` (period) written
INSIDE it, never by its filename - a renamed file once silently dropped
all revenue.

Input:  data/sources/xbrl/frames_*.json         (python fetch_sec_xbrl.py)
        data/sources/xbrl/company_tickers.json  (ticker, for eye-checking)
Output: data/cache/xbrl_clean.csv

Usage:
    python clean_xbrl.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# concept -> SEC tags in priority order (first one a company uses wins)
CONCEPTS = {
    "rd_expense": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",   # current standard
        "Revenues",                                              # older tag
        "RevenueFromContractWithCustomerIncludingAssessedTax",   # includes sales tax
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "short_term_investments": [
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    ],
}
TAG_TO_CONCEPT = {t: c for c, tags in CONCEPTS.items() for t in tags}
TAG_RANK = {t: i for tags in CONCEPTS.values() for i, t in enumerate(tags)}

OUT_COLUMNS = ["company", "cik", "ticker", "concept", "period", "period_type",
               "period_start", "period_end", "value_usd", "tag", "accession",
               "location"]


def period_type(ccp: str) -> str:
    """CY2025 -> year, CY2026Q2I -> instant (one day), CY2025Q3 -> quarter."""
    if ccp.endswith("I"):
        return "instant"
    return "quarter" if "Q" in ccp else "year"


def load_frames(src: Path) -> list:
    """Every usable frame file, one per (tag, period). If two files hold the
    same tag and period, the one with more companies is kept."""
    best = {}
    for path in sorted(src.glob("frames_*.json")):
        try:
            frame = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"  skipped {path.name}: unreadable ({e})")
            continue
        tag, ccp = frame.get("tag"), frame.get("ccp")
        if tag not in TAG_TO_CONCEPT or not ccp:
            continue                          # a concept we do not use
        if frame.get("uom") not in (None, "USD"):
            continue                          # shares, percentages, ...
        n = len(frame.get("data") or [])
        if (tag, ccp) not in best or n > best[(tag, ccp)][1]:
            best[(tag, ccp)] = (frame, n)
    return [f for f, _ in best.values()]


def load_tickers(src: Path) -> dict:
    """CIK -> the common-stock ticker (no dash, then shortest)."""
    path = src / "company_tickers.json"
    if not path.exists():
        return {}
    out = {}
    for row in json.loads(path.read_text(encoding="utf-8")).values():
        try:
            cik, t = int(row["cik_str"]), str(row["ticker"]).upper()
        except (KeyError, TypeError, ValueError):
            continue
        if cik not in out or ("-" in t, len(t), t) < ("-" in out[cik], len(out[cik]), out[cik]):
            out[cik] = t
    return out


def clean(src: Path) -> pd.DataFrame:
    frames = load_frames(src)
    if not frames:
        raise FileNotFoundError(f"No XBRL frames in {src}\n"
                                "Run 'python fetch_sec_xbrl.py' first.")
    tickers = load_tickers(src)
    rows = []
    for frame in frames:
        tag, ccp = frame["tag"], frame["ccp"]
        for d in frame.get("data") or []:
            if d.get("cik") is None or d.get("val") is None:
                continue
            rows.append({
                "company": d.get("entityName"),
                "cik": int(d["cik"]),
                "ticker": tickers.get(int(d["cik"])),
                "concept": TAG_TO_CONCEPT[tag],
                "period": ccp,
                "period_type": period_type(ccp),
                "period_start": d.get("start"),
                "period_end": d.get("end"),
                "value_usd": d["val"],
                "tag": tag,
                "accession": d.get("accn"),
                "location": d.get("loc"),
                "_rank": TAG_RANK[tag],
            })
    df = pd.DataFrame(rows)
    # One figure per company, concept and period: the highest-priority tag
    # that has a NON-ZERO figure, else zero. A few companies file $0 under
    # the ASC 606 tag and their real revenue under "Revenues" (8 cases).
    # A zero on every tag stays zero - Amylyx really had no 2025 revenue
    # after withdrawing its drug.
    df["_zero"] = df["value_usd"] == 0
    df = (df.sort_values(["cik", "concept", "period", "_zero", "_rank"])
            .drop_duplicates(["cik", "concept", "period"], keep="first")
            .drop(columns=["_rank", "_zero"]))
    df = df.sort_values(["cik", "concept", "period_end", "period"])
    return df.reindex(columns=OUT_COLUMNS).reset_index(drop=True)


def main() -> int:
    from nexus.settings import Settings
    cfg = Settings()
    src = cfg.sources_dir / "xbrl"
    dst = cfg.cache_dir / "xbrl_clean.csv"
    print(f"\n=== NexusAI: clean SEC XBRL figures ===\n\n  reading {src}")
    out = clean(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.to_csv(dst, index=False, encoding="utf-8")
    except PermissionError as e:
        raise PermissionError(f"Could not write {dst.name} - is it open in "
                              "Excel? Close it and re-run.") from e
    print(f"  rows                            {len(out):>8,}")
    for concept in CONCEPTS:
        c = out[out["concept"] == concept]
        periods = ", ".join(sorted(c["period"].unique())) or "none - not fetched yet"
        print(f"  {concept:<24} {c['cik'].nunique():>6,} companies   {periods}")
    print(f"\n  written: {dst}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
