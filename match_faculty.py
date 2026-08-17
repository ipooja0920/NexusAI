#!/usr/bin/env python3
"""Stage 1 - Match faculty against the enriched company datasource.

Processes every faculty row with Flag = N (or a specific one via
--faculty). For each: embedding pre-filter -> LLM alignment scoring ->
top-40 partnership/funding scoring (cached across faculty) -> final
weighted score -> per-faculty output workbook -> Flag flipped to Y.

Usage:
    python match_faculty.py
    python match_faculty.py --dry-run
    python match_faculty.py --faculty "Ioulia Valla"
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from nexus.cache import Caches
from nexus.excel_io import (read_enriched, read_faculty, set_faculty_flag,
                            write_faculty_output)
from nexus.prefilter import embed_text, rank_by_similarity, select_candidates
from nexus.scoring import (FUNDING_SYSTEM_PROMPT, PARTNERSHIP_SYSTEM_PROMPT,
                           normalize, score_alignment, score_secondary)
from nexus.settings import Settings


def build_research_profile(fac: dict) -> str:
    parts = []
    if fac.get("research"):
        parts.append(fac["research"])
    if fac.get("department"):
        parts.append(f"Department: {fac['department']}")
    classes = ", ".join(x for x in (fac.get("class1"), fac.get("class2")) if x)
    if classes:
        parts.append(f"Industry areas: {classes}")
    return "\n".join(parts)


def safe_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " ._-").strip()


def get_secondary_scores(client, cfg, caches, company: str, description):
    """Partnership + funding scores, from cache when available."""
    cached = caches.get_company_scores(company)
    if cached is not None:
        return cached["partnership"], cached["funding"], True

    model = cfg.openai["scoring_model"]
    retries = cfg.openai["max_retries"]
    rate = cfg.openai["rate_limit_seconds"]
    p_score, p_reason = score_secondary(
        client, model, company, PARTNERSHIP_SYSTEM_PROMPT, description,
        max_retries=retries, rate_limit=rate)
    f_score, f_reason = score_secondary(
        client, model, company, FUNDING_SYSTEM_PROMPT, description,
        max_retries=retries, rate_limit=rate)
    partnership = {"score": p_score, "reason": p_reason}
    funding = {"score": f_score, "reason": f_reason}
    caches.set_company_scores(company, partnership, funding)
    return partnership, funding, False


def to_num(score_str):
    """'7' -> 7 ; 'NA'/None -> None."""
    try:
        return int(score_str)
    except (TypeError, ValueError):
        return None


def match_one_faculty(fac: dict, companies, cfg: Settings, caches: Caches,
                      client) -> str:
    """Run the full funnel for one faculty. Returns output file path."""
    n_companies = len(companies)
    profile = build_research_profile(fac)
    print(f"\n{'='*60}\nFaculty: {fac['name']}  ({fac['department']})")
    print(f"Profile: {profile[:100]}{'...' if len(profile) > 100 else ''}")

    # ---------- Pre-filter ----------
    top_n = cfg.prefilter["top_n_candidates"] if cfg.prefilter["enabled"] else 0
    if top_n and top_n > 0 and n_companies > top_n:
        profile_vec = embed_text(client, cfg.openai["embedding_model"], profile, caches)
        company_vecs = []
        for c in companies:
            from nexus.prefilter import company_embedding_text
            text = company_embedding_text(c["company"], c.get("description"))
            company_vecs.append(caches.get_embedding(text))
        n_missing = sum(1 for v in company_vecs if v is None)
        if n_missing:
            print(f"  [prefilter] WARNING: {n_missing} companies have no embedding "
                  "(re-run enrich_companies.py) - they rank last in pre-filter")
        sims = rank_by_similarity(profile_vec, company_vecs)
        candidate_idx = select_candidates(sims, top_n)
        print(f"  [prefilter] {n_companies} companies -> {len(candidate_idx)} candidates")
    else:
        candidate_idx = list(range(n_companies))
        print(f"  [prefilter] disabled - scoring all {n_companies} companies")

    # ---------- Alignment scoring ----------
    model = cfg.openai["scoring_model"]
    retries = cfg.openai["max_retries"]
    rate = cfg.openai["rate_limit_seconds"]

    align = {}   # global company index -> {"score": str, "reason": str}
    for k, gi in enumerate(candidate_idx):
        c = companies[gi]
        score, reason = score_alignment(
            client, model, c["company"], c.get("description"), profile,
            max_retries=retries, rate_limit=rate)
        align[gi] = {"score": score, "reason": reason}
        if (k + 1) % 20 == 0 or (k + 1) == len(candidate_idx):
            print(f"  [alignment] {k+1}/{len(candidate_idx)} scored")

    # ---------- Preliminary score -> top 40 ----------
    w = cfg.weights
    align_nums = [to_num(align[gi]["score"]) if gi in align else None
                  for gi in range(n_companies)]
    align_norm = normalize(align_nums)

    prelim_weight = w["distance"] + w["employees"] + w["revenue"] + w["alignment"]
    prelim = []
    for gi in range(n_companies):
        if gi not in align:
            prelim.append(-1.0)  # not a candidate
            continue
        c = companies[gi]
        val = (w["distance"] * c["distance_score"] +
               w["employees"] * c["employee_score"] +
               w["revenue"] * c["revenue_score"] +
               w["alignment"] * align_norm[gi]) / prelim_weight
        prelim.append(val)

    cutoff = cfg.funnel["secondary_cutoff"]
    ranked = sorted((gi for gi in candidate_idx), key=lambda i: prelim[i], reverse=True)
    top_secondary = ranked[:cutoff]
    print(f"  [funnel] top {len(top_secondary)} selected for partnership/funding scoring")

    # ---------- Secondary scoring (cached across faculty) ----------
    secondary = {}
    n_cache_hits = 0
    for k, gi in enumerate(top_secondary):
        c = companies[gi]
        partnership, funding, was_cached = get_secondary_scores(
            client, cfg, caches, c["company"], c.get("description"))
        secondary[gi] = {"partnership": partnership, "funding": funding}
        n_cache_hits += was_cached
        if (k + 1) % 10 == 0 or (k + 1) == len(top_secondary):
            print(f"  [secondary] {k+1}/{len(top_secondary)} done "
                  f"({n_cache_hits} from cache)")
    caches.save_all()

    # ---------- Final weighted score ----------
    p_nums = [to_num(secondary[gi]["partnership"]["score"]) if gi in secondary else None
              for gi in range(n_companies)]
    f_nums = [to_num(secondary[gi]["funding"]["score"]) if gi in secondary else None
              for gi in range(n_companies)]
    p_norm = normalize(p_nums)
    f_norm = normalize(f_nums)

    overall = {}
    for gi in top_secondary:
        c = companies[gi]
        overall[gi] = (w["distance"] * c["distance_score"] +
                       w["employees"] * c["employee_score"] +
                       w["revenue"] * c["revenue_score"] +
                       w["alignment"] * align_norm[gi] +
                       w["partnership"] * p_norm[gi] +
                       w["funding"] * f_norm[gi])

    final_n = cfg.funnel["final_top_n"]
    final_ranked = sorted(overall, key=overall.get, reverse=True)[:final_n]

    # ---------- Build output ----------
    results = []
    for rank, gi in enumerate(final_ranked, start=1):
        c = companies[gi]
        sec = secondary[gi]
        results.append({
            "rank": rank,
            "company": c["company"],
            "website": c.get("website"),
            "distance_miles": c.get("distance_miles"),
            "employees": c.get("employees"),
            "revenue_usdmm": c.get("revenue_usdmm"),
            "distance_score": c["distance_score"],
            "employee_score": c["employee_score"],
            "revenue_score": c["revenue_score"],
            "alignment_score": align[gi]["score"],
            "alignment_reason": align[gi]["reason"],
            "partnership_score": sec["partnership"]["score"],
            "partnership_reason": sec["partnership"]["reason"],
            "funding_score": sec["funding"]["score"],
            "funding_reason": sec["funding"]["reason"],
            "overall_score": overall[gi],
            "description": c.get("description"),
        })

    run_info = {
        "research_profile": profile,
        "scoring_model": model,
        "embedding_model": cfg.openai["embedding_model"],
        "n_companies": n_companies,
        "n_candidates": len(candidate_idx),
        "secondary_cutoff": cutoff,
        "weights": w,
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = cfg.output_dir / f"{safe_filename(fac['name'])} Match {stamp}.xlsx"
    write_faculty_output(out_path, fac, results, run_info)
    print(f"  [output] {out_path}")
    return str(out_path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Match faculty to companies.")
    ap.add_argument("--faculty", help="Run only this faculty name (ignores flag).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would run without any API calls.")
    args = ap.parse_args()

    cfg = Settings()
    caches = Caches(cfg.cache_dir)

    print("\n=== NexusAI Stage 1: Faculty Matching ===\n")

    faculty_all = read_faculty(cfg.faculty_database, cfg.faculty)
    if args.faculty:
        target = args.faculty.strip().lower()
        todo = [f for f in faculty_all if f["name"].lower() == target]
        if not todo:
            print(f"ERROR: Faculty '{args.faculty}' not found in the database.")
            print("Names available: " + ", ".join(f["name"] for f in faculty_all))
            return 1
    else:
        todo = [f for f in faculty_all if f["flag"] == "N"]

    if not todo:
        print("No faculty with Flag = N. Nothing to do.")
        return 0

    print(f"Faculty to process: {', '.join(f['name'] for f in todo)}")

    companies_df = read_enriched(cfg.companies_enriched)
    companies = companies_df.where(companies_df.notna(), None).to_dict("records")
    print(f"Company datasource: {len(companies)} companies (enriched)")

    if args.dry_run:
        top_n = cfg.prefilter["top_n_candidates"] if cfg.prefilter["enabled"] else 0
        n_cand = min(top_n, len(companies)) if top_n else len(companies)
        est = n_cand + 2 * min(cfg.funnel["secondary_cutoff"], n_cand)
        print(f"\n[dry-run] Per faculty: ~{n_cand} alignment calls + up to "
              f"{2 * cfg.funnel['secondary_cutoff']} secondary calls "
              f"(minus cache hits) = ~{est} LLM calls max.")
        print(f"[dry-run] {len(todo)} faculty x ~{est} = "
              f"~{len(todo) * est} calls upper bound. No API was called.")
        return 0

    cfg.require_openai_key()
    from openai import OpenAI
    client = OpenAI()

    t0 = time.time()
    done = 0
    for fac in todo:
        try:
            match_one_faculty(fac, companies, cfg, caches, client)
            set_faculty_flag(cfg.faculty_database, cfg.faculty, fac["row"], "Y")
            print(f"  [flag] {fac['name']} -> Y")
            done += 1
        except KeyboardInterrupt:
            print("\nInterrupted. Completed faculty are flagged Y; re-run to resume.")
            caches.save_all()
            return 130
        except Exception as e:
            print(f"  [ERROR] {fac['name']}: {e}")
            print("  Flag left at N - this faculty will retry on next run.")

    caches.save_all()
    print(f"\n{'='*60}")
    print(f"Completed {done}/{len(todo)} faculty in {time.time()-t0:.0f}s.")
    print(f"Outputs in: {cfg.output_dir}")
    return 0 if done == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
