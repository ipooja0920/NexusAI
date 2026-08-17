#!/usr/bin/env python3
"""Stage 0 - Enrich the Capital IQ company datasource.

Run this once after placing/refreshing data/companies_raw.xlsx.

For every company: clean address -> geocode (cached) -> distance from
UConn Storrs -> component scores; compute a description embedding (cached).
Writes data/companies_enriched.xlsx. Re-running after a datasource refresh
only pays for NEW/changed companies - everything else hits the caches.

Usage:
    python enrich_companies.py
    python enrich_companies.py --skip-geocode    # testing without network
    python enrich_companies.py --skip-embeddings # testing without API key
"""
from __future__ import annotations

import argparse
import sys
import time

from nexus.cache import Caches, _text_key
from nexus.excel_io import read_capitaliq, write_enriched
from nexus.geo import Geocoder, clean_address_block, distance_miles, select_address_block
from nexus.prefilter import company_embedding_text, embed_text
from nexus.scoring import (distance_score, employee_score, parse_employees,
                           parse_revenue, revenue_score)
from nexus.settings import Settings


def main() -> int:
    ap = argparse.ArgumentParser(description="Enrich the company datasource.")
    ap.add_argument("--skip-geocode", action="store_true",
                    help="Skip geocoding (distances blank). Testing only.")
    ap.add_argument("--skip-embeddings", action="store_true",
                    help="Skip embeddings (pre-filter won't work). Testing only.")
    args = ap.parse_args()

    cfg = Settings()
    caches = Caches(cfg.cache_dir)

    print("\n=== NexusAI Stage 0: Datasource Enrichment ===\n")
    print(f"Reading Capital IQ export: {cfg.companies_raw}")
    df = read_capitaliq(cfg.companies_raw, cfg.capitaliq)
    n = len(df)
    if n == 0:
        print("ERROR: No companies found in the datasource.")
        return 1
    print(f"Found {n} companies.\n")

    # ---- OpenAI client (only needed for embeddings) ----
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

    for i, row in df.iterrows():
        company = row["company"]

        # ---- address -> distance ----
        cleaned = clean_address_block(select_address_block(row["address"]))
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
        emp = parse_employees(row["employees"])
        rev = parse_revenue(row["revenue"])
        d_score = distance_score(dist, comp_cfg["distance_decay"])
        e_score = employee_score(emp, comp_cfg["employee_cap"])
        r_score = revenue_score(rev, comp_cfg["revenue"])

        # ---- embedding ----
        emb_key = ""
        if client is not None:
            text = company_embedding_text(company, row["description"])
            was_cached = caches.get_embedding(text) is not None
            vec = embed_text(client, cfg.openai["embedding_model"], text, caches)
            if vec is not None:
                emb_key = _text_key(text)
                stats["emb_cached" if was_cached else "embedded"] += 1

        records.append({
            "company": company,
            "website": row["website"],
            "description": row["description"],
            "address_cleaned": cleaned,
            "latitude": lat, "longitude": lon,
            "geocode_provider": provider,
            "distance_miles": dist,
            "employees": emp if emp > 0 else None,
            "revenue_usdmm": rev,
            "distance_score": round(d_score, 6),
            "employee_score": round(e_score, 6),
            "revenue_score": round(r_score, 6),
            "embedding_key": emb_key,
        })

        done = i + 1
        if done % 25 == 0 or done == n:
            print(f"  [{done}/{n}] processed ({time.time()-t0:.0f}s elapsed)")
            caches.save_all()  # periodic save so interrupts lose nothing

    write_enriched(cfg.companies_enriched, records)
    caches.save_all()

    print(f"\nEnriched file written: {cfg.companies_enriched}")
    print(f"Geocoding: {stats['geocoded']} new, {stats['cached']} from cache, "
          f"{stats['failed']} failed")
    print(f"Embeddings: {stats['embedded']} new, {stats['emb_cached']} from cache")
    print(f"Done in {time.time()-t0:.0f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
