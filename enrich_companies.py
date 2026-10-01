#!/usr/bin/env python3
"""Stage 0 - Enrich the companies: data/companies_combined.xlsx -> data/companies_enriched.xlsx

Reads the COMBINED file (Capital IQ + SBIR, Form D, SEC XBRL,
ClinicalTrials.gov and OpenAlex, built by combine_companies.py) and, for
every company:

  address  -> geocode (cached) -> miles from UConn Storrs -> distance_score
  revenue  -> revenue_score   (size fit: full marks $100m - $1bn)
  employees-> employee_score
  sources  -> funding_score   (money available now: Form D since 2025,
                               SEC cash and R&D, running SBIR awards,
                               university research it funded recently)
           -> academic_adjustment (track record with universities: added
                               to the AI partnership score at match time)
  text     -> research_focus (trial conditions, OpenAlex topics, SBIR
              project titles) and an embedding of name + description +
              research focus (cached)

Every source column of the combined file is carried across, so the
enriched sheet shows the evidence behind each score.

Re-running only pays for NEW or changed companies - geocodes and embeddings
are cached.

Usage:
    python combine_companies.py        # first, whenever a source changes
    python enrich_companies.py
    python enrich_companies.py --skip-geocode    # testing without network
    python enrich_companies.py --skip-embeddings # testing without API key
"""
from __future__ import annotations

import argparse
import sys
import time

import pandas as pd
from openpyxl.utils import column_index_from_string

from nexus.cache import Caches, _text_key
from nexus.excel_io import write_enriched
from nexus.funding import company_academic_adjustment, company_funding_score
from nexus.geo import Geocoder, clean_address_block, distance_miles, select_address_block
from nexus.prefilter import company_embedding_text, embed_text
from nexus.scoring import (distance_score, employee_score, parse_employees,
                           parse_revenue, revenue_score)
from nexus.settings import Settings

# source columns of the combined file the scores read (the rest stay in the
# combined file only)
SOURCE_PREFIXES = ("employees_source", "revenue_source", "sbir_", "university_",
                   "formd_", "xbrl_", "ct_", "oa_")

# combined-file columns carried into the enriched sheet: only those a score
# is computed from, so each score sits beside its evidence
SCORE_INPUTS = [
    "employees_source", "revenue_source",          # which figure was scored
    # funding_score
    "formd_recent_usdmm", "formd_raised",
    "xbrl_cash_total_usdmm", "xbrl_cash_as_of",
    "xbrl_rd_expense_usdmm", "xbrl_rd_year",
    "sbir_active_usdmm",
    "oa_funded_university", "oa_last_funded_year",
    # academic_adjustment
    "oa_coauthored_university", "sbir_awarded",
    "university_past", "university_current",
    "ct_university_past", "ct_university_current",
]
CT_ACTIVE = "ct_trials_active"   # only carried when its penalty is switched on

BASE_COLUMNS = [
    "company", "website", "description", "address_cleaned", "company_type",
    "industry", "latitude", "longitude", "geocode_provider", "distance_miles",
    "employees", "revenue_usdmm",
    "distance_score", "employee_score", "revenue_score",
    "funding_score", "funding_reason", "academic_adjustment", "academic_reason",
    "research_focus",
]

FOCUS_TITLES = 3          # newest SBIR project titles in research_focus
FOCUS_MAX = 1500          # characters


def capitaliq_names(cfg) -> dict:
    """field -> Capital IQ header text, from the column letters in config.
    The combined file keeps Capital IQ's own headers, so the letters are
    resolved against the raw export's header row."""
    ciq = cfg.capitaliq
    hdr = list(pd.read_excel(cfg.companies_raw, header=int(ciq["header_row"]) - 1,
                             nrows=0).columns)
    fields = {"company": "col_company", "website": "col_website",
              "address": "col_address", "description": "col_description",
              "employees": "col_employees", "revenue": "col_revenue",
              "industry": "col_industry", "company_type": "col_company_type"}
    return {f: hdr[column_index_from_string(str(ciq[k])) - 1]
            for f, k in fields.items() if ciq.get(k)}


def research_focus(row: dict) -> str | None:
    """What the company researches, from the sources that know it."""
    def txt(k):
        v = row.get(k)
        return None if v is None or (isinstance(v, float) and v != v) else str(v).strip() or None
    parts = []
    if txt("oa_topics"):
        parts.append(f"Research topics: {txt('oa_topics')}")
    if txt("ct_conditions"):
        parts.append(f"Clinical trial conditions: {txt('ct_conditions')}")
    if txt("sbir_award_titles"):
        titles = txt("sbir_award_titles").split(" | ")[:FOCUS_TITLES]
        parts.append("SBIR projects: " + " | ".join(titles))
    text = ". ".join(parts)
    return text[:FOCUS_MAX] or None


def main() -> int:
    ap = argparse.ArgumentParser(description="Enrich the company datasource.")
    ap.add_argument("--skip-geocode", action="store_true",
                    help="Skip geocoding (distances blank). Testing only.")
    ap.add_argument("--skip-embeddings", action="store_true",
                    help="Skip embeddings (pre-filter won't work). Testing only.")
    args = ap.parse_args()

    cfg = Settings()
    caches = Caches(cfg.cache_dir)
    combined_path = cfg.path("companies_combined", "data/companies_combined.xlsx")

    print("\n=== NexusAI Stage 0: Datasource Enrichment ===\n")
    if not combined_path.exists():
        print(f"ERROR: {combined_path} not found.\n"
              "  Run 'python combine_companies.py' first - it attaches the source\n"
              "  data (SBIR, Form D, SEC, ClinicalTrials.gov, OpenAlex) to Capital IQ.")
        return 1
    print(f"Reading combined file: {combined_path}")
    df = pd.read_excel(combined_path, sheet_name="Companies")
    df = df.astype(object).where(df.notna(), None)
    # pandas reads a True/False column with blanks as 1.0 / 0.0 - put the
    # True / False back, so the sheet reads as the combined file does
    for col in ("oa_funded_university", "oa_coauthored_university"):
        if col in df.columns:
            df[col] = df[col].map(lambda v: None if v is None else bool(v))
    n = len(df)
    if n == 0:
        print("ERROR: No companies found in the combined file.")
        return 1
    names = capitaliq_names(cfg)
    source_cols = [c for c in df.columns if c.startswith(SOURCE_PREFIXES)]
    print(f"Found {n} companies, {len(source_cols)} source columns.\n")

    client = None
    if not args.skip_embeddings:
        cfg.require_openai_key()
        from openai import OpenAI
        client = OpenAI()

    geocoder = None
    if not args.skip_geocode:
        geocoder = Geocoder(
            user_agent=cfg.geocoding["nominatim_user_agent"],
            rate_limit_seconds=cfg.geocoding["rate_limit_seconds"],
            caches=caches,
        )

    reference = cfg.geocoding["reference_point"]
    comp_cfg = cfg.components
    records = []
    stats = {"geocoded": 0, "cached": 0, "failed": 0, "embedded": 0, "emb_cached": 0}
    t0 = time.time()

    for i, row in enumerate(df.to_dict("records")):
        g = lambda f: row.get(names[f]) if f in names else None   # noqa: E731
        company = str(g("company")).strip()

        # ---- address -> distance ----
        cleaned = clean_address_block(select_address_block(g("address")))
        lat = lon = dist = None
        provider = "skipped"
        if geocoder and cleaned:
            already_cached = caches.get_geocode(cleaned) is not None
            lat, lon, provider = geocoder.geocode(cleaned)
            if already_cached:
                stats["cached"] += 1
            elif provider == "failed":
                stats["failed"] += 1
            else:
                stats["geocoded"] += 1
            dist = distance_miles(lat, lon, reference)

        # ---- numeric components ----
        emp = parse_employees(g("employees"))
        rev = parse_revenue(g("revenue"))
        focus = research_focus(row)

        record = {
            "company": company,
            "website": g("website"),
            "description": g("description"),
            "address_cleaned": cleaned,
            "company_type": g("company_type"),
            "industry": g("industry"),
            "latitude": lat, "longitude": lon,
            "geocode_provider": provider,
            "distance_miles": dist,
            "employees": emp if emp > 0 else None,
            "revenue_usdmm": rev,
            "distance_score": round(distance_score(dist, comp_cfg["distance_decay"]), 6),
            "employee_score": round(employee_score(emp, comp_cfg["employee_cap"]), 6),
            "revenue_score": round(revenue_score(rev, comp_cfg["revenue"]), 6),
            "research_focus": focus,
        }
        record.update({c: row.get(c) for c in source_cols})

        # Scores that need no API call live in the enriched sheet, beside
        # the evidence they were computed from.
        record["funding_score"], record["funding_reason"] = \
            company_funding_score(record, cfg.funding)
        record["academic_adjustment"], record["academic_reason"] = \
            company_academic_adjustment(record, cfg.academic)

        # ---- embedding ----
        record["embedding_key"] = ""
        if client is not None:
            text = company_embedding_text(company, record["description"], focus)
            was_cached = caches.get_embedding(text) is not None
            vec = embed_text(client, cfg.openai["embedding_model"], text, caches)
            if vec is not None:
                record["embedding_key"] = _text_key(text)
                stats["emb_cached" if was_cached else "embedded"] += 1

        records.append(record)
        done = i + 1
        if done % 250 == 0 or done == n:
            print(f"  [{done}/{n}] processed ({time.time()-t0:.0f}s elapsed)")
            caches.save_all()   # periodic save so interrupts lose nothing

    inputs = SCORE_INPUTS + ([CT_ACTIVE] if cfg.academic.get("ct_active_penalty") else [])
    columns = BASE_COLUMNS + [c for c in inputs if c in df.columns] + ["embedding_key"]
    write_enriched(cfg.companies_enriched, records, columns)
    caches.save_all()

    f = pd.Series([r["funding_score"] for r in records])
    a = pd.Series([r["academic_adjustment"] for r in records])
    print(f"\nEnriched file written: {cfg.companies_enriched}")
    print(f"Geocoding: {stats['geocoded']} new, {stats['cached']} from cache, "
          f"{stats['failed']} failed")
    print(f"Embeddings: {stats['embedded']} new, {stats['emb_cached']} from cache")
    print(f"Funding score: {(f > cfg.funding['missing_score']).sum():,} above neutral, "
          f"{(f < cfg.funding['missing_score']).sum():,} below, "
          f"{(f == cfg.funding['missing_score']).sum():,} neutral (no record)")
    print(f"Academic adjustment: {(a > 0).sum():,} positive, {(a < 0).sum():,} negative")
    print(f"\nDone in {time.time()-t0:.0f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
