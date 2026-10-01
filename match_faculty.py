#!/usr/bin/env python3
"""Stage 1 - Match faculty against the enriched company datasource.

Faculty come from the BD-IFR Faculty Registration export (CSV) - pick one
with --row (its spreadsheet row; the header is row 1) or --faculty "Name" -
or, with neither, from the old Faculty Database (every row with Flag = N).

Companies are ranked separately in three size tiers (config size_tiers):
  Small  under $100m revenue, or revenue unknown (~21,500):
         tab 1 WITH the pre-filter - embedding similarity -> the closest 300
         small companies -> AI alignment score;
         tab 2 WITHOUT it (unless --no-full) - every small company with a
         description, `openai.concurrency` AI calls at a time
  Mid    $100m - $1bn (~190): all AI-scored
  Large  $1bn and above (~80): all AI-scored
In each tab: preliminary score -> top 40 get the AI partnership score
(cached across faculty) -> final weighted score -> top 20. Every top-20
company gets a written justification. One workbook per faculty: a tab per
tier (and pass), All Companies, Run Info.

Alignment scores are cached (faculty profile x company text), so an
interrupted run picks up where it stopped and the second pass reuses the
first pass's 300 scores.

Usage:
    python match_faculty.py --row 13
    python match_faculty.py --faculty "Julie Burland"
    python match_faculty.py --row 13 --sample 200   # cost/speed test first
    python match_faculty.py --row 13 --no-full      # 300 pre-filter tab only
    python match_faculty.py                         # Faculty Database, Flag = N
    python match_faculty.py --row 13 --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from nexus import scoring
from nexus.cache import Caches
from nexus.excel_io import (read_enriched, read_faculty, set_faculty_flag,
                            unique_path, write_faculty_output)
from nexus.faculty_registration import (read_registration, research_profile,
                                        sector_match)
from nexus.funding import company_academic_adjustment, company_funding_score
from nexus.prefilter import (company_embedding_text, company_profile_text,
                             embed_text, rank_by_similarity, select_candidates)
from nexus.scoring import (PARTNERSHIP_SYSTEM_PROMPT, normalize, score_alignment,
                           score_secondary, write_justification)
from nexus.settings import Settings

ALIGN_VERSION = "v2"           # bump when the alignment prompt changes
NO_TEXT_REASON = "No company description available - not scored"
REGISTRATION_DEFAULT = "data/input/BDIFR Faculty Registration From-documents.csv"


# ----------------------------------------------------------------------
# Faculty
# ----------------------------------------------------------------------

def build_research_profile(fac: dict) -> str:
    if fac.get("source") == "registration":
        return research_profile(fac)
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


def company_text(c: dict) -> str:
    """What the AI reads about a company: the enriched company profile
    (Capital IQ description + SBIR / trials / OpenAlex), or for an older
    enriched file the description + research focus."""
    prof = c.get("company_profile")
    if prof and str(prof).strip() not in ("", "-", "nan", "None"):
        return str(prof).strip()
    return company_profile_text(c.get("description"), c.get("research_focus"))


def to_num(score_str):
    """'7' -> 7 ; 'NA'/None -> None."""
    try:
        return int(score_str)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# AI calls, parallel and cached
# ----------------------------------------------------------------------

def score_alignments(indices, companies, texts, profile, cfg, caches, client, align):
    """Fill `align` {company index: {"score", "reason"}} for `indices`."""
    model = cfg.openai["scoring_model"]
    todo = []
    for gi in indices:
        if gi in align:
            continue
        if not texts[gi]:
            align[gi] = {"score": "NA", "reason": NO_TEXT_REASON}
            continue
        c = companies[gi]
        key = f"{ALIGN_VERSION}|{model}|{profile}|||{c['company']}|{c.get('industry')}|{texts[gi]}"
        hit = caches.get_alignment(key)
        if hit:
            align[gi] = hit
        else:
            todo.append((gi, key))
    if not todo:
        return 0
    n, t0 = len(todo), time.time()
    step = 20 if n <= 400 else 500

    def one(gi):
        c = companies[gi]
        return score_alignment(client, model, c["company"], texts[gi], profile,
                               max_retries=cfg.openai["max_retries"],
                               rate_limit=cfg.openai["rate_limit_seconds"],
                               industry=c.get("industry"))

    done = 0
    with ThreadPoolExecutor(max_workers=int(cfg.openai.get("concurrency", 16))) as pool:
        futures = {pool.submit(one, gi): (gi, key) for gi, key in todo}
        for fut in as_completed(futures):
            gi, key = futures[fut]
            score, reason = fut.result()
            align[gi] = {"score": score, "reason": reason}
            if score != "NA" or reason != scoring.STD_NA_REASON:
                caches.set_alignment(key, align[gi])
            done += 1
            if done % step == 0 or done == n:
                print(f"  [alignment] {done}/{n} scored ({time.time()-t0:.0f}s)")
            if done % 2000 == 0:
                caches.alignment.save()
    caches.alignment.save()
    failed = sum(1 for gi, _ in todo if align[gi]["reason"] == scoring.STD_NA_REASON)
    if failed:
        print(f"  [alignment] {failed} got no usable answer (shown as NA) - they are "
              "not cached, so a re-run retries just those")
    return n


def partnership_scores(indices, companies, texts, cfg, caches, client, memo):
    """Fill `memo` {company index: partnership dict} - AI, cached by
    company + profile text, so it is shared across faculty."""
    model = cfg.openai["scoring_model"]
    todo = []
    for gi in indices:
        if gi in memo:
            continue
        hit = caches.get_company_scores(companies[gi]["company"], texts[gi])
        if hit is not None:
            memo[gi] = hit["partnership"]
        else:
            todo.append(gi)

    def one(gi):
        return score_secondary(client, model, companies[gi]["company"],
                               PARTNERSHIP_SYSTEM_PROMPT, texts[gi],
                               max_retries=cfg.openai["max_retries"],
                               rate_limit=cfg.openai["rate_limit_seconds"])

    with ThreadPoolExecutor(max_workers=int(cfg.openai.get("concurrency", 16))) as pool:
        futures = {pool.submit(one, gi): gi for gi in todo}
        for fut in as_completed(futures):
            gi = futures[fut]
            score, reason = fut.result()
            memo[gi] = {"score": score, "reason": reason}
            caches.set_company_scores(companies[gi]["company"], memo[gi],
                                      {"score": None, "reason": "computed at match time"},
                                      texts[gi])
    caches.company_scores.save()
    return len(indices) - len(todo), len(todo)


# ----------------------------------------------------------------------
# One pass: preliminary score -> top 40 partnership -> final top 20
# ----------------------------------------------------------------------

def run_pass(candidate_idx, companies, texts, align, partner, cfg, caches, client):
    w = cfg.weights
    n = len(companies)
    cand = set(candidate_idx)
    align_nums = [to_num(align[gi]["score"]) if gi in cand and gi in align else None
                  for gi in range(n)]
    align_norm = normalize(align_nums)

    prelim_weight = w["distance"] + w["employees"] + w["revenue"] + w["alignment"]
    prelim = {}
    for gi in candidate_idx:
        c = companies[gi]
        prelim[gi] = (w["distance"] * c["distance_score"] +
                      w["employees"] * c["employee_score"] +
                      w["revenue"] * c["revenue_score"] +
                      w["alignment"] * align_norm[gi]) / prelim_weight

    cutoff = cfg.funnel["secondary_cutoff"]
    ranked = sorted(candidate_idx, key=lambda i: prelim[i], reverse=True)
    top_secondary = ranked[:cutoff]
    cached, new = partnership_scores(top_secondary, companies, texts, cfg, caches,
                                     client, partner)
    print(f"  [partnership] top {len(top_secondary)}: {cached} from cache, {new} new")

    p_nums = [to_num(partner[gi]["score"]) if gi in top_secondary else None
              for gi in range(n)]
    p_norm = normalize(p_nums)
    funding = {gi: company_funding_score(companies[gi], cfg.funding) for gi in top_secondary}
    academic = {gi: company_academic_adjustment(companies[gi], cfg.academic)
                for gi in top_secondary}
    # Funding is NOT normalised (0.5 already means "no evidence"); the
    # academic nudge is added after normalising so it keeps its size.
    p_adj = {gi: min(1.0, max(0.0, p_norm[gi] + academic[gi][0])) for gi in top_secondary}
    overall = {}
    for gi in top_secondary:
        c = companies[gi]
        overall[gi] = (w["distance"] * c["distance_score"] +
                       w["employees"] * c["employee_score"] +
                       w["revenue"] * c["revenue_score"] +
                       w["alignment"] * align_norm[gi] +
                       w["partnership"] * p_adj[gi] +
                       w["funding"] * funding[gi][0])
    final = sorted(overall, key=overall.get, reverse=True)[:cfg.funnel["final_top_n"]]
    return {"candidates": list(candidate_idx), "prelim": prelim, "secondary": top_secondary,
            "funding": funding, "academic": academic, "overall": overall, "final": final}


# ----------------------------------------------------------------------
# Justification
# ----------------------------------------------------------------------

def _fmt(v, unit=""):
    if v is None or (isinstance(v, float) and v != v):
        return "unknown"
    return f"{v:,.0f}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"


def facts_for(gi, c, text, sector, align, partner, funding, academic) -> str:
    return "\n".join([
        f"Industry: {c.get('industry') or 'unknown'}"
        + (f" ({sector} named by the researcher)" if sector else ""),
        f"Company profile: {text or '(no description)'}",
        f"Alignment score {align['score']}/9: {align['reason']}",
        f"Partnership score {partner['score']}/9: {partner['reason']}",
        f"University track record: {academic[1]}",
        f"Money available: {funding[1]}",
        f"Distance from UConn Storrs: {_fmt(c.get('distance_miles'), ' miles')}",
        f"Revenue: {_fmt(c.get('revenue_usdmm'))} $USDmm; employees: {_fmt(c.get('employees'))}",
    ])


def justifications(indices, companies, texts, sectors, profile, align, partner,
                   funding, academic, cfg, client) -> dict:
    model = cfg.openai["scoring_model"]

    def one(gi):
        facts = facts_for(gi, companies[gi], texts[gi], sectors[gi], align[gi],
                          partner[gi], funding[gi], academic[gi])
        text = write_justification(client, model, profile, companies[gi]["company"],
                                   facts, max_retries=cfg.openai["max_retries"])
        # if the AI fails, the scores' own reasons still say why
        return text or f"Alignment {align[gi]['score']}/9: {align[gi]['reason']}"

    out = {}
    with ThreadPoolExecutor(max_workers=int(cfg.openai.get("concurrency", 16))) as pool:
        futures = {pool.submit(one, gi): gi for gi in indices}
        for fut in as_completed(futures):
            out[futures[fut]] = fut.result()
    return out


# ----------------------------------------------------------------------
# One faculty
# ----------------------------------------------------------------------

TIERS = ("Small", "Mid", "Large")


def size_tier(revenue, tiers_cfg: dict) -> str:
    """Small: under small_below $USDmm or no revenue figure; Mid: up to
    large_from; Large: large_from and above."""
    try:
        v = float(revenue)
    except (TypeError, ValueError):
        return "Small"
    if v != v or v < tiers_cfg["small_below"]:
        return "Small"
    return "Mid" if v < tiers_cfg["large_from"] else "Large"


def _money(v) -> str:
    """$USDmm -> '$100m' / '$1bn'."""
    return f"${v / 1000:g}bn" if v >= 1000 else f"${v:g}m"


def tier_titles(tiers_cfg: dict) -> dict:
    lo, hi = tiers_cfg["small_below"], tiers_cfg["large_from"]
    return {"Mid": f"Mid ({_money(lo)}-{_money(hi)})", "Large": f"Large ({_money(hi)}+)"}


def match_one_faculty(fac: dict, companies, cfg: Settings, caches: Caches, client,
                      full: bool = True, sample: int = 0) -> str:
    """One faculty, ranked separately within each company size tier:
      Small (under $100m or revenue unknown): the closest `top_n_candidates`
          small companies by embedding similarity, and - unless --no-full -
          every small company with a description, as a second tab
      Mid ($100m-$1bn) and Large ($1bn+): few enough to AI-score them all
    Each tier gets its own top 40 (partnership) and top 20."""
    n = len(companies)
    profile = build_research_profile(fac)
    print(f"\n{'='*60}\nFaculty: {fac['name']}  ({fac.get('department')})")
    print(f"Profile: {profile[:160]}{'...' if len(profile) > 160 else ''}")
    texts = [company_text(c) for c in companies]
    sectors = [sector_match(c.get("industry"), fac) for c in companies]
    tiers_cfg = cfg.raw.get("size_tiers") or {"small_below": 100, "large_from": 1000}
    tier = [size_tier(c.get("revenue_usdmm"), tiers_cfg) for c in companies]
    members = {t: [gi for gi in range(n) if tier[gi] == t] for t in TIERS}
    print("  [size] " + ", ".join(f"{t}: {len(members[t]):,}" for t in TIERS))
    titles = tier_titles(tiers_cfg)
    scoring.reset_usage()
    t_start = time.time()
    align, partner = {}, {}
    passes = []          # (tab title, tier, pass result)

    # ---------- Small: pre-filter ----------
    small = members["Small"]
    top_n = cfg.prefilter["top_n_candidates"] if cfg.prefilter["enabled"] else 0
    sims = None
    if top_n and top_n > 0 and len(small) > top_n:
        profile_vec = embed_text(client, cfg.openai["embedding_model"], profile, caches)
        company_vecs = [caches.get_embedding(company_embedding_text(c["company"], texts[i]))
                        for i, c in enumerate(companies)]
        n_missing = sum(1 for v in company_vecs if v is None)
        if n_missing:
            print(f"  [prefilter] WARNING: {n_missing} companies have no embedding "
                  "(re-run enrich_companies.py) - they rank last in pre-filter")
        sims = rank_by_similarity(profile_vec, company_vecs)
        cand_a = [gi for gi in select_candidates(sims, 0) if tier[gi] == "Small"][:top_n]
        print(f"  [prefilter] {len(small):,} small companies -> {len(cand_a)} candidates")
    else:
        cand_a = list(small)
    pre_label = f"{len(cand_a)} pre-filter" if len(cand_a) < len(small) else "all companies"

    if small:
        print(f"\n  Small - {pre_label}")
        score_alignments(cand_a, companies, texts, profile, cfg, caches, client, align)
        passes.append((f"Small - {pre_label}", "Small",
                       run_pass(cand_a, companies, texts, align, partner, cfg, caches, client)))

    # ---------- Small: every company (no pre-filter) ----------
    if small and full and len(cand_a) < len(small):
        with_text = [gi for gi in small if texts[gi]]
        if sample and sims is not None:
            in_a = set(cand_a)
            rest = [gi for gi in select_candidates(sims, 0)
                    if tier[gi] == "Small" and gi not in in_a and texts[gi]]
            cand_b = cand_a + rest[:sample]
            title = f"Small - sample {len(cand_b)}"
        else:
            cand_b = with_text
            title = "Small - all companies"
        print(f"\n  {title}: {len(cand_b):,} companies "
              f"({len(small) - len(with_text):,} small companies have no description)")
        t_b = time.time()
        calls_before = scoring.USAGE["calls"]
        made = score_alignments(cand_b, companies, texts, profile, cfg, caches, client, align)
        secs = time.time() - t_b
        if sample and made:
            remaining = len(with_text) - len(cand_b)
            per = secs / made
            calls = max(1, scoring.USAGE["calls"] - calls_before)
            print(f"\n  [sample] {made} new alignment calls in {secs:.0f}s "
                  f"({per:.2f}s each with {cfg.openai.get('concurrency', 16)} in parallel)")
            print(f"  [sample] tokens per call: {scoring.USAGE['prompt_tokens'] / calls:,.0f} in, "
                  f"{scoring.USAGE['completion_tokens'] / calls:,.0f} out (incl. reasoning)")
            print(f"  [sample] the full pass would need ~{remaining:,} more calls: "
                  f"about {remaining * per / 60:.0f} minutes")
        passes.append((title, "Small",
                       run_pass(cand_b, companies, texts, align, partner, cfg, caches, client)))

    # ---------- Mid and Large: score them all ----------
    for t in ("Mid", "Large"):
        if not members[t]:
            continue
        print(f"\n  {titles[t]}: all {len(members[t]):,} companies")
        score_alignments(members[t], companies, texts, profile, cfg, caches, client, align)
        passes.append((titles[t], t, run_pass(members[t], companies, texts, align, partner,
                                              cfg, caches, client)))

    # ---------- Justifications for every top-20 company ----------
    finals = list(dict.fromkeys(gi for _, _, p in passes for gi in p["final"]))
    funding, academic = {}, {}
    for _, _, p in passes:
        funding.update(p["funding"])
        academic.update(p["academic"])
    print(f"\n  [justification] writing {len(finals)} ...")
    just = justifications(finals, companies, texts, sectors, profile, align, partner,
                          funding, academic, cfg, client)

    # ---------- Build output ----------
    def row(gi, rank=None):
        c = companies[gi]
        a = align.get(gi)
        p = partner.get(gi)
        return {
            "rank": rank, "company": c["company"], "size": tier[gi],
            "justification": just.get(gi),
            "sector_match": sectors[gi] or None, "website": c.get("website"),
            "distance_miles": c.get("distance_miles"), "employees": c.get("employees"),
            "revenue_usdmm": c.get("revenue_usdmm"),
            "distance_score": c["distance_score"], "employee_score": c["employee_score"],
            "revenue_score": c["revenue_score"],
            "alignment_score": a["score"] if a else None,
            "alignment_reason": a["reason"] if a else None,
            "partnership_score": p["score"] if p else None,
            "partnership_reason": (f'{p["reason"]} | Track record {academic[gi][1]}'
                                   if p and gi in academic else (p["reason"] if p else None)),
            "funding_score": funding[gi][0] if gi in funding else None,
            "funding_reason": funding[gi][1] if gi in funding else None,
            "company_profile": texts[gi] or None,
        }

    tabs = {}
    for title, _, p in passes:
        rows = []
        for rank, gi in enumerate(p["final"], start=1):
            r = row(gi, rank)
            r["overall_score"] = p["overall"][gi]
            rows.append(r)
        tabs[title] = rows

    # All Companies: every company, from the widest pass of its size tier
    widest = {}
    for _, t, p in passes:
        widest[t] = p          # later passes of a tier are the wider ones
    final_n, cutoff = cfg.funnel["final_top_n"], cfg.funnel["secondary_cutoff"]
    in_a = set(cand_a)

    def stage_of(gi, w):
        if w is None:
            return "Not scored"
        if gi in w["rank"]:
            return f"Top {final_n}"
        if gi in w["sec"]:
            return f"Secondary scored (top {cutoff})"
        if gi in w["cand"]:
            if align.get(gi, {}).get("reason") == NO_TEXT_REASON:
                return "Not scored (no description)"
            return "Alignment scored"
        if not texts[gi]:
            return "Not scored (no description)"
        return "Filtered out (pre-filter)"

    info = {t: {"rank": {gi: k for k, gi in enumerate(p["final"], start=1)},
                "sec": set(p["secondary"]), "cand": set(p["candidates"]), "p": p}
            for t, p in widest.items()}

    def sort_key(gi):
        w = info.get(tier[gi])
        order = TIERS.index(tier[gi])
        if w and gi in w["rank"]:
            return (order, 0, w["rank"][gi])
        if w and gi in w["cand"]:
            return (order, 1, -w["p"]["prelim"][gi])
        return (order, 2, gi)

    all_rows = []
    for gi in sorted(range(n), key=sort_key):
        w = info.get(tier[gi])
        r = row(gi, w["rank"].get(gi) if w else None)
        r["stage"] = stage_of(gi, w)
        r["in_prefilter"] = ("Yes" if gi in in_a else "No") if tier[gi] == "Small" else "All scored"
        r["overall_score"] = w["p"]["overall"].get(gi) if w else None
        if not w or gi not in w["sec"]:
            r["partnership_score"] = r["partnership_reason"] = None
        all_rows.append(r)

    run_info = {
        "research_profile": profile,
        "faculty_source": (f"Registration form, row {fac['row']}"
                           if fac.get("source") == "registration" else "Faculty Database"),
        "scoring_model": cfg.openai["scoring_model"],
        "embedding_model": cfg.openai["embedding_model"],
        "n_companies": n,
        "size_tiers": ", ".join(f"{t}: {len(members[t]):,}" for t in TIERS)
                      + f" (Small = under {_money(tiers_cfg['small_below'])} or revenue "
                      f"unknown; Large = {_money(tiers_cfg['large_from'])} and above)",
        "n_candidates": f"{len(cand_a)} small companies",
        "n_full": "; ".join(f"{title}: {len(p['candidates']):,}" for title, _, p in passes),
        "secondary_cutoff": f"{cutoff} per tab",
        "weights": cfg.weights,
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = unique_path(cfg.output_dir / f"{safe_filename(fac['name'])} Match {stamp}.xlsx")
    write_faculty_output(out_path, fac, tabs, run_info, all_rows=all_rows)
    caches.save_all()
    u = scoring.USAGE
    print(f"\n  [usage] {u['calls']:,} AI calls, {u['prompt_tokens']:,} tokens in, "
          f"{u['completion_tokens']:,} out, {time.time()-t_start:.0f}s")
    print(f"  [output] {out_path}")
    return str(out_path)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def pick_faculty(args, cfg):
    """-> (faculty list, error message or None)."""
    reg_path = cfg.path("faculty_registration", REGISTRATION_DEFAULT)
    if args.row is not None:
        reg = read_registration(reg_path)
        todo = [f for f in reg if f["row"] == args.row]
        if not todo:
            return [], (f"No faculty on row {args.row} of {reg_path.name}. Rows: "
                        + ", ".join(f"{f['row']} {f['name']}" for f in reg))
        return todo, None
    if args.faculty:
        target = " ".join(args.faculty.split()).lower()
        pools = []
        if reg_path.exists():
            pools.append(read_registration(reg_path))
        if cfg.faculty_database.exists():
            pools.append(read_faculty(cfg.faculty_database, cfg.faculty))
        for pool in pools:
            todo = [f for f in pool if " ".join(f["name"].split()).lower() == target]
            if todo:
                return todo[:1], None
        names = [f["name"] for pool in pools for f in pool]
        return [], (f"Faculty '{args.faculty}' not found in the database.\n"
                    "Names available: " + ", ".join(names))
    return [f for f in read_faculty(cfg.faculty_database, cfg.faculty) if f["flag"] == "N"], None


def main() -> int:
    ap = argparse.ArgumentParser(description="Match faculty to companies.")
    ap.add_argument("--faculty", help="Run this faculty (registration form first, "
                                      "then the Faculty Database).")
    ap.add_argument("--row", type=int, help="Run the faculty on this row of the "
                                           "registration form (header = row 1).")
    ap.add_argument("--no-full", action="store_true",
                    help="Only the 300 pre-filter pass (skip scoring every company).")
    ap.add_argument("--sample", type=int, default=0,
                    help="Second pass on the pre-filter + N more companies only, and "
                         "print what the full pass would take.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would run without any API calls.")
    args = ap.parse_args()

    cfg = Settings()
    caches = Caches(cfg.cache_dir)
    print("\n=== NexusAI Stage 1: Faculty Matching ===\n")

    todo, err = pick_faculty(args, cfg)
    if err:
        print(f"ERROR: {err}")
        return 1
    if not todo:
        print("No faculty with Flag = N. Nothing to do.")
        return 0
    print(f"Faculty to process: {', '.join(f['name'] for f in todo)}")

    companies_df = read_enriched(cfg.companies_enriched)
    companies = companies_df.where(companies_df.notna(), None).to_dict("records")
    print(f"Company datasource: {len(companies)} companies (enriched)")
    full = not args.no_full and cfg.prefilter.get("full_run", True)

    if args.dry_run:
        tiers_cfg = cfg.raw.get("size_tiers") or {"small_below": 100, "large_from": 1000}
        tier = [size_tier(c.get("revenue_usdmm"), tiers_cfg) for c in companies]
        has = [bool(company_text(c)) for c in companies]
        top_n = cfg.prefilter["top_n_candidates"] if cfg.prefilter["enabled"] else 0
        n_small = sum(1 for t in tier if t == "Small")
        n_small_text = sum(1 for t, h in zip(tier, has) if t == "Small" and h)
        n_cand = min(top_n, n_small) if top_n else n_small
        n_ml = sum(1 for t, h in zip(tier, has) if t != "Small" and h)
        extra = (min(n_small_text, n_cand + args.sample) if args.sample else n_small_text) - n_cand
        print(f"\n[dry-run] Size tiers: " + ", ".join(
            f"{t} {sum(1 for x in tier if x == t):,}" for t in TIERS))
        print(f"[dry-run] Alignment calls: ~{n_cand} small (pre-filter) + {n_ml} mid/large"
              + (f" + up to ~{max(0, extra):,} more small (no pre-filter)" if full else "")
              + f"; up to {cfg.funnel['secondary_cutoff']} partnership and "
              f"{cfg.funnel['final_top_n']} justification calls per tab; minus cache hits.")
        print("[dry-run] No API was called.")
        return 0

    cfg.require_openai_key()
    from openai import OpenAI
    client = OpenAI()

    t0 = time.time()
    done = 0
    for fac in todo:
        try:
            match_one_faculty(fac, companies, cfg, caches, client, full=full,
                              sample=args.sample)
            if fac.get("source") != "registration":
                set_faculty_flag(cfg.faculty_database, cfg.faculty, fac["row"], "Y")
                print(f"  [flag] {fac['name']} -> Y")
            done += 1
        except KeyboardInterrupt:
            print("\nInterrupted. Scores so far are cached - re-run to resume.")
            caches.save_all()
            return 130
        except Exception as e:
            print(f"  [ERROR] {fac['name']}: {e}")
            caches.save_all()
            if fac.get("source") != "registration":
                print("  Flag left at N - this faculty will retry on next run.")

    caches.save_all()
    print(f"\n{'='*60}")
    print(f"Completed {done}/{len(todo)} faculty in {time.time()-t0:.0f}s.")
    print(f"Outputs in: {cfg.output_dir}")
    return 0 if done == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
