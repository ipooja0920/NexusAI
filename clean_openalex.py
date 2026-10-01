#!/usr/bin/env python3
"""OpenAlex research ties, cleaned  ->  two files in data/cache/

  openalex_companies.csv   one row per OpenAlex US company
      company, website, city, research topics, papers it wrote per year
      and papers it funded per year (since 2005)

  openalex_ties.csv        one row per company per UNIVERSITY / HOSPITAL
      role        funded      - the university wrote papers the company paid for
                  coauthored  - the company's own staff wrote papers with it
      papers_2005_2023, papers_2024_on, papers_total

A "university" here is an OpenAlex institution of type education or
healthcare (universities, medical schools, hospitals) - the same group
ClinicalTrials.gov's partner columns hold. Companies, government bodies,
facilities and non-profits on the same papers are left out. All countries
are kept; country is a column.

No matching to Capital IQ here - that happens at the combine step. No paper
titles or author names were ever downloaded: only counts.

Input:  data/sources/openalex/ties/            (python fetch_openalex_ties.py)
        data/sources/openalex/openalex_raw_institutions.json (topics, city)
Output: data/cache/openalex_companies.csv, data/cache/openalex_ties.csv

Usage:
    python clean_openalex.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

UNIVERSITY_TYPES = {"education", "healthcare"}
TOP_TOPICS = 5

COMPANY_COLUMNS = ["institution_id", "funder_id", "company", "website", "city",
                   "topics", "papers_since_2005", "papers_by_year",
                   "funded_papers_since_2005", "funded_papers_by_year"]
TIE_COLUMNS = ["institution_id", "funder_id", "company", "role", "university",
               "university_id", "university_type", "university_country",
               "papers_2005_2023", "papers_2024_on", "papers_total"]


def short_id(url) -> str:
    return str(url or "").rsplit("/", 1)[-1]


def by_year(answer: dict | None) -> tuple:
    """(total, "2025:40; 2024:35; ...") newest first, years with 0 left out."""
    if not answer:
        return None, None
    years = sorted(((int(g["key"]), g["count"]) for g in answer["groups"] if g["count"]),
                   reverse=True)
    return answer["count"], "; ".join(f"{y}:{n}" for y, n in years) or None


def clean(src: Path, raw_institutions: Path) -> tuple:
    ties_dir = src
    orgs = json.loads((ties_dir / "organisations.json").read_text(encoding="utf-8"))
    types = json.loads((ties_dir / "institution_types.json").read_text(encoding="utf-8"))
    inst = {short_id(r["id"]): r for r in
            json.loads(raw_institutions.read_text(encoding="utf-8"))}

    def answer(key, q):
        p = ties_dir / f"{key}_{q}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    companies, ties = [], []
    for o in orgs:
        key = o["institution"] or o["funder"]
        rec = inst.get(o["institution"] or "", {})
        topics = sorted(rec.get("topics") or [], key=lambda t: -(t.get("count") or 0))
        own_total, own_years = by_year(answer(key, "coauthored_by_year"))
        fund_total, fund_years = by_year(answer(key, "funded_by_year"))
        companies.append({
            "institution_id": o["institution"], "funder_id": o["funder"],
            "company": o["name"], "website": o["website"], "city": o["city"],
            "topics": "; ".join(t["display_name"] for t in topics[:TOP_TOPICS]) or None,
            "papers_since_2005": own_total, "papers_by_year": own_years,
            "funded_papers_since_2005": fund_total, "funded_papers_by_year": fund_years,
        })
        for role in ("funded", "coauthored"):
            past, recent = answer(key, f"{role}_past"), answer(key, f"{role}_recent")
            if past is None and recent is None:
                continue
            counts = {}
            for part, a in (("past", past), ("recent", recent)):
                for g in (a or {}).get("groups") or []:
                    counts.setdefault(short_id(g["key"]), {"past": 0, "recent": 0})[part] += g["count"]
            for uid, c in counts.items():
                t = types.get(uid) or {}
                if t.get("type") not in UNIVERSITY_TYPES:
                    continue
                ties.append({
                    "institution_id": o["institution"], "funder_id": o["funder"],
                    "company": o["name"], "role": role, "university": t.get("name"),
                    "university_id": uid, "university_type": t.get("type"),
                    "university_country": t.get("country"),
                    "papers_2005_2023": c["past"], "papers_2024_on": c["recent"],
                    "papers_total": c["past"] + c["recent"],
                })
    comp = pd.DataFrame(companies, columns=COMPANY_COLUMNS)
    for col in ("papers_since_2005", "funded_papers_since_2005"):
        comp[col] = comp[col].astype("Int64")
    tie = pd.DataFrame(ties, columns=TIE_COLUMNS).sort_values(
        ["company", "role", "papers_total"], ascending=[True, True, False]).reset_index(drop=True)
    return comp, tie


def main() -> int:
    from nexus.settings import Settings
    cfg = Settings()
    src = cfg.sources_dir / "openalex"
    print(f"\n=== NexusAI: clean OpenAlex research ties ===\n\n  reading {src / 'ties'}")
    comp, tie = clean(src / "ties", src / "openalex_raw_institutions.json")
    out_c, out_t = cfg.cache_dir / "openalex_companies.csv", cfg.cache_dir / "openalex_ties.csv"
    try:
        comp.to_csv(out_c, index=False, encoding="utf-8")
        tie.to_csv(out_t, index=False, encoding="utf-8")
    except PermissionError as e:
        raise PermissionError("Could not write - is a file open in Excel? Close it and re-run.") from e
    f, c = tie[tie.role == "funded"], tie[tie.role == "coauthored"]
    print(f"  companies                          {len(comp):>8,}")
    print(f"    that wrote papers since 2005     {(comp.papers_since_2005.fillna(0) > 0).sum():>8,}")
    print(f"    that funded papers since 2005    {(comp.funded_papers_since_2005.fillna(0) > 0).sum():>8,}")
    print(f"  company-university ties (funded)   {len(f):>8,}   ({f.company.nunique():,} companies)")
    print(f"  company-university ties (co-wrote) {len(c):>8,}   ({c.company.nunique():,} companies)")
    print(f"\n  written: {out_c}\n           {out_t}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
