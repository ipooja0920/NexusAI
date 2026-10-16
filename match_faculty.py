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
import re
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
from nexus.funding import _yes, company_academic_adjustment, company_funding_score
from nexus.prefilter import (company_embedding_text, company_profile_text,
                             embed_text, rank_by_similarity, select_candidates)
from nexus.scoring import (PARTNERSHIP_SYSTEM_PROMPT, normalize, score_alignment,
                           score_secondary, write_justification)
from nexus.settings import Settings

ALIGN_VERSION = "v4"           # bump when the alignment prompt changes
PARTNER_VERSION = "p2"         # bump when the partnership prompt changes
EXCLUDE_DEFAULT = ("Acquired", "Out of Business", "Liquidating")
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
    pool = ThreadPoolExecutor(max_workers=int(cfg.openai.get("concurrency", 16)))
    try:
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
            if done % 500 == 0:
                caches.alignment.save()
    except KeyboardInterrupt:
        # Ctrl-C: drop the queued calls instead of waiting for all of them,
        # and keep every score already made for the re-run
        print("\n  [alignment] stopping - saving the scores made so far ...")
        pool.shutdown(wait=False, cancel_futures=True)
        caches.alignment.save()
        raise
    pool.shutdown(wait=True)
    caches.alignment.save()
    failed = sum(1 for gi, _ in todo if align[gi]["reason"] == scoring.STD_NA_REASON)
    if failed:
        print(f"  [alignment] {failed} got no usable answer (shown as NA) - they are "
              "not cached, so a re-run retries just those")
    return n


def _short(v, n=5) -> str:
    items = [x.strip() for x in str(v).split(";") if x.strip()]
    more = f" (+{len(items) - n} more)" if len(items) > n else ""
    return "; ".join(items[:n]) + more


def track_record_text(c: dict) -> str:
    """The company's university record from SBIR, ClinicalTrials.gov and
    OpenAlex, in words - so the partnership judgement rests on evidence, not
    on whether the model has heard of the company."""
    def has(k):
        v = c.get(k)
        return v is not None and not (isinstance(v, float) and v != v) and \
            str(v).strip() not in ("", "-", "nan", "None")
    lines = []
    if has("university_past"):
        lines.append(f"Past university partners on SBIR/STTR awards: {_short(c['university_past'])}")
    if has("university_current"):
        lines.append(f"Current university partners on SBIR/STTR awards: {_short(c['university_current'])}")
    if has("ct_university_past"):
        lines.append(f"Past university partners on clinical trials: {_short(c['ct_university_past'])}")
    if has("ct_university_current"):
        lines.append(f"Current university partners on clinical trials: {_short(c['ct_university_current'])}")
    if _yes(c.get("oa_funded_university")):
        yr = c.get("oa_last_funded_year")
        lines.append("Paid for research papers written by universities"
                     + (f" (latest {int(float(yr))})" if has("oa_last_funded_year") else ""))
    if _yes(c.get("oa_coauthored_university")):
        lines.append("Co-wrote research papers with universities")
    if str(c.get("sbir_awarded") or "") == "Yes":
        lines.append("Has won federal SBIR/STTR research awards")
    if not lines:
        return ("University track record: none found in SBIR, ClinicalTrials.gov "
                "or OpenAlex records.")
    return "University track record (SBIR, ClinicalTrials.gov, OpenAlex):\n- " + "\n- ".join(lines)


def partnership_text(c: dict, text: str) -> str:
    return f"{text}\n\n{track_record_text(c)}".strip() if text else track_record_text(c)


def _partner_key(c: dict, text: str) -> str:
    """Cache key text: the prompt version + what the model was given."""
    return f"{PARTNER_VERSION}\n{partnership_text(c, text)}"


def partnership_scores(indices, companies, texts, cfg, caches, client, memo):
    """Fill `memo` {company index: partnership dict} - AI, cached by
    company + profile text, so it is shared across faculty."""
    model = cfg.openai["scoring_model"]
    todo = []
    ptexts = {gi: partnership_text(companies[gi], texts[gi]) for gi in indices}
    pkeys = {gi: _partner_key(companies[gi], texts[gi]) for gi in indices}
    for gi in indices:
        if gi in memo:
            continue
        hit = caches.get_company_scores(companies[gi]["company"], pkeys[gi])
        if hit is not None:
            memo[gi] = hit["partnership"]
        else:
            todo.append(gi)

    def one(gi):
        return score_secondary(client, model, companies[gi]["company"],
                               PARTNERSHIP_SYSTEM_PROMPT, ptexts[gi],
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
                                      pkeys[gi])
    caches.company_scores.save()
    return len(indices) - len(todo), len(todo)


# ----------------------------------------------------------------------
# One pass: preliminary score -> top 40 partnership -> final top 20
# ----------------------------------------------------------------------

PROVIDERS = ("Hospital / health system", "Clinic / care provider")
PROVIDER_TAB = "Hospitals & Clinics"


def is_provider(c: dict) -> bool:
    """Hospitals, health systems, clinics, practices - listed on their own sheet."""
    return (c.get("organisation_type") or "") in PROVIDERS


def tier_min_alignment(cfg, tier: str) -> float:
    """Minimum AI alignment score for a tier (funnel.min_alignment_by_tier,
    else funnel.min_alignment; 0 = no minimum)."""
    by_tier = cfg.funnel.get("min_alignment_by_tier") or {}
    if tier in by_tier:
        return by_tier[tier] or 0
    return cfg.funnel.get("min_alignment", 0) or 0


def verdict(score) -> str:
    """The plain-words verdict a justification starts with, from the 1-9
    alignment score."""
    v = to_num(score)
    if v is None:
        return "Fit unclear"
    return "Strong fit" if v >= 7 else "Moderate fit" if v >= 5 else "Weak fit"


def run_pass(candidate_idx, companies, texts, align, partner, cfg, caches, client,
             min_align=None):
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

    # optional floor on the AI alignment score (per tier, 0 = off)
    if min_align is None:
        min_align = cfg.funnel.get("min_alignment", 0) or 0
    eligible = [gi for gi in candidate_idx
                if align_nums[gi] is not None and align_nums[gi] >= min_align]
    cutoff = cfg.funnel["secondary_cutoff"]
    ranked = sorted(eligible, key=lambda i: prelim[i], reverse=True)
    # companies and care providers (hospitals, clinics) go forward separately,
    # so the providers - listed on their own sheet - never take the places of
    # companies, and vice versa
    comp = [gi for gi in ranked if not is_provider(companies[gi])][:cutoff]
    prov = [gi for gi in ranked if is_provider(companies[gi])][:cutoff]
    top_secondary = sorted(comp + prov, key=lambda i: prelim[i], reverse=True)
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
    # An NA partnership answer means "not known", not "unlikely": it counts as
    # neutral (like a missing funding record), not as the lowest score
    p_missing = cfg.funnel.get("partnership_missing", 0.5)
    p_base = {gi: (p_norm[gi] if p_nums[gi] is not None else p_missing) for gi in top_secondary}
    p_adj = {gi: min(1.0, max(0.0, p_base[gi] + academic[gi][0])) for gi in top_secondary}
    overall = {}
    for gi in top_secondary:
        c = companies[gi]
        overall[gi] = (w["distance"] * c["distance_score"] +
                       w["employees"] * c["employee_score"] +
                       w["revenue"] * c["revenue_score"] +
                       w["alignment"] * align_norm[gi] +
                       w["partnership"] * p_adj[gi] +
                       w["funding"] * funding[gi][0])
    final_n = cfg.funnel["final_top_n"]
    final_comp = sorted(comp, key=overall.get, reverse=True)[:final_n]
    final_prov = sorted(prov, key=overall.get, reverse=True)[:final_n]
    final = sorted(final_comp + final_prov, key=overall.get, reverse=True)
    return {"candidates": list(candidate_idx), "prelim": prelim, "secondary": top_secondary,
            "eligible": set(eligible), "funding": funding, "academic": academic,
            "overall": overall, "final": final,
            "final_companies": final_comp, "final_providers": final_prov}


# ----------------------------------------------------------------------
# Justification
# ----------------------------------------------------------------------

def _fmt(v, unit=""):
    if v is None or (isinstance(v, float) and v != v):
        return "unknown"
    return f"{v:,.0f}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"


def facts_for(gi, c, text, sector, align, partner, funding, academic) -> str:
    org = c.get("organisation_type") or "Company"
    status = c.get("company_status")
    parent = c.get("parent_company")
    return "\n".join([
        f"Verdict to start with: {verdict(align['score'])}",
        f"Organisation type: {org}"
        + (f"; Capital IQ status: {status}" if status else "")
        + (f"; subsidiary of {parent}" if parent else ""),
        f"Industry: {c.get('industry') or 'unknown'}"
        + (f" ({sector} named by the researcher)" if sector else ""),
        f"Company profile: {text or '(no description)'}",
        f"Alignment score (AI, from the company profile): {align['score']}/9",
        (f"Partnership likelihood score: {partner['score']}/9"
         if partner["score"] != "NA" else
         "Partnership likelihood: not known (counted as neutral)"),
        track_record_text(c),
        f"Money available: {funding[1]}",
        f"Distance from UConn Storrs: {_fmt(c.get('distance_miles'), ' miles')}",
        f"Revenue: {_fmt(c.get('revenue_usdmm'))} $USDmm; employees: {_fmt(c.get('employees'))}",
    ])


def plain_justification(c: dict, align: dict, funding: tuple) -> str:
    """A justification made only of our data, for when the written one
    cannot be trusted."""
    tr = track_record_text(c).replace("\n- ", "; ").replace(":;", ":")
    return (f"{verdict(align['score'])}: alignment {align['score']}/9 (AI, from the company "
            f"profile): {align['reason']} "
            f"{tr}. Money: {funding[1]} Distance from Storrs: "
            f"{_fmt(c.get('distance_miles'), ' miles')}.")


def justifications(indices, companies, texts, sectors, profile, align, partner,
                   funding, academic, cfg, client) -> dict:
    model = cfg.openai["scoring_model"]

    def one(gi):
        facts = facts_for(gi, companies[gi], texts[gi], sectors[gi], align[gi],
                          partner[gi], funding[gi], academic[gi])
        text = write_justification(client, model, profile, companies[gi]["company"],
                                   facts, max_retries=cfg.openai["max_retries"])
        # the AI failed or kept adding things not in our data: write it plainly
        # from the data instead
        if not text:
            return plain_justification(companies[gi], align[gi], funding[gi])
        v = verdict(align[gi]["score"])
        return text if text.lower().startswith(v.lower()) else f"{v}: {text}"

    out = {}
    with ThreadPoolExecutor(max_workers=int(cfg.openai.get("concurrency", 16))) as pool:
        futures = {pool.submit(one, gi): gi for gi in indices}
        for fut in as_completed(futures):
            out[futures[fut]] = fut.result()
    return out


# ----------------------------------------------------------------------
# One faculty
# ----------------------------------------------------------------------

def prefilter_queries(fac: dict, profile: str) -> list:
    """(label, text) for the embedding pre-filter: the full profile, then -
    for registration-form faculty - each keyword and the stated industry
    interests on their own.

    One embedding of a long research description is dominated by its
    longest theme: for Julie Burland the ACL paragraph buried "wearables",
    and Whoop and Empatica ranked 460th and 852nd."""
    queries = [("profile", profile)]
    if fac.get("source") == "registration":
        kws = [k.strip() for k in re.split(r"[,;\n]+", fac.get("keywords") or "") if k.strip()]
        for kw in kws[:8]:
            queries.append((f"keyword: {kw}", kw))
        if (fac.get("notes") or "").strip():
            queries.append(("industry interests", fac["notes"].strip()))
    return queries


def combine_searches(ranked_by: list, top_n: int, extra: int) -> tuple:
    """The full-profile search keeps its top `top_n` exactly as before; each
    other search ADDS up to `extra` companies of its own on top (ones the
    profile search did not already pick). -> (candidates, {index: label of
    the search that brought it in})."""
    chosen, found_by = [], {}
    for k, (label, ranked) in enumerate(ranked_by):
        limit = top_n if k == 0 else extra
        added = 0
        for gi in ranked:
            if added >= limit:
                break
            if gi not in found_by:
                found_by[gi] = label
                chosen.append(gi)
                added += 1
    return chosen, found_by


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
    # Companies Capital IQ says are acquired, out of business or liquidating
    # are left out of every tab (they still appear in All Companies)
    exclude = set(cfg.funnel.get("exclude_status", EXCLUDE_DEFAULT) or [])
    excluded = {gi for gi, c in enumerate(companies)
                if str(c.get("company_status") or "").strip() in exclude}
    if excluded:
        print(f"  [status] {len(excluded):,} companies left out "
              f"({', '.join(sorted(exclude))} in Capital IQ)")
    members = {t: [gi for gi in range(n) if tier[gi] == t and gi not in excluded]
               for t in TIERS}
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
    found_by = {}
    if top_n and top_n > 0 and len(small) > top_n:
        company_vecs = [caches.get_embedding(company_embedding_text(c["company"], texts[i]))
                        for i, c in enumerate(companies)]
        n_missing = sum(1 for v in company_vecs if v is None)
        if n_missing:
            print(f"  [prefilter] WARNING: {n_missing} companies have no embedding "
                  "(re-run enrich_companies.py) - they rank last in pre-filter")
        in_small = set(small)
        ranked_by = []                      # (label, picks per round, ranked list)
        for label, text in prefilter_queries(fac, profile):
            vec = embed_text(client, cfg.openai["embedding_model"], text, caches)
            q_sims = rank_by_similarity(vec, company_vecs)
            if label == "profile":
                sims = q_sims
            ranked_by.append((label, [gi for gi in select_candidates(q_sims, 0) if gi in in_small]))
        cand_a, found_by = combine_searches(ranked_by, top_n,
                                            int(cfg.prefilter.get("extra_per_search", 30)))
        counts = {}
        for lab in found_by.values():
            counts[lab] = counts.get(lab, 0) + 1
        print(f"  [prefilter] {len(small):,} small companies -> {len(cand_a)} candidates "
              f"({', '.join(f'{k}: {v}' for k, v in counts.items())})")
    else:
        cand_a = list(small)
    pre_label = f"{top_n} pre-filter" if len(cand_a) < len(small) else "all companies"

    if small:
        print(f"\n  Small - {pre_label}")
        score_alignments(cand_a, companies, texts, profile, cfg, caches, client, align)
        passes.append((f"Small - {pre_label}", "Small",
                       run_pass(cand_a, companies, texts, align, partner, cfg, caches, client,
                                min_align=tier_min_alignment(cfg, "Small"))))

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
                       run_pass(cand_b, companies, texts, align, partner, cfg, caches, client,
                                min_align=tier_min_alignment(cfg, "Small"))))

    # ---------- Mid and Large: score them all ----------
    for t in ("Mid", "Large"):
        if not members[t]:
            continue
        print(f"\n  {titles[t]}: all {len(members[t]):,} companies")
        score_alignments(members[t], companies, texts, profile, cfg, caches, client, align)
        passes.append((titles[t], t, run_pass(members[t], companies, texts, align, partner,
                                              cfg, caches, client,
                                              min_align=tier_min_alignment(cfg, t))))

    # ---------- One tab per size tier, from its widest pass ----------
    final_n, cutoff = cfg.funnel["final_top_n"], cfg.funnel["secondary_cutoff"]
    widest = {}
    for title, t, p in passes:
        widest[t] = (title, p)             # later passes of a tier are the wider ones

    def tab_title(t, title):
        if t == "Small":
            how = ("all companies" if title.startswith("Small - all")
                   else "sample" if "sample" in title else "pre-filter")
            return f"Small - top {final_n} ({how})"
        return f"{titles[t]} - top {final_n}"

    # ---------- Justifications for every listed company ----------
    finals = list(dict.fromkeys(gi for t in TIERS if t in widest for gi in widest[t][1]["final"]))
    # providers beyond the combined sheet's top N are not listed - no justification
    prov_all = sorted(((widest[t][1]["overall"][gi], gi) for t in TIERS if t in widest
                       for gi in widest[t][1]["final_providers"]), reverse=True)
    unlisted = {gi for _, gi in prov_all[final_n:]}
    finals = [gi for gi in finals if gi not in unlisted]
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
            "organisation_type": c.get("organisation_type") or "Company",
            "company_status": c.get("company_status"),
            "parent_company": c.get("parent_company"),
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
    for t in TIERS:
        if t not in widest:
            continue
        title, p = widest[t]
        rows = []
        for rank, gi in enumerate(p["final_companies"], start=1):
            r = row(gi, rank)
            r["overall_score"] = p["overall"][gi]
            rows.append(r)
        tabs[tab_title(t, title)] = rows
    # hospitals and clinics of every size on one sheet, best first
    prov = sorted(((p["overall"][gi], gi) for t in TIERS if t in widest
                   for p in [widest[t][1]] for gi in p["final_providers"]), reverse=True)
    rows = []
    for rank, (score, gi) in enumerate(prov[:final_n], start=1):
        r = row(gi, rank)
        r["overall_score"] = score
        rows.append(r)
    tabs[f"{PROVIDER_TAB} - top {final_n}"] = rows
    listed = {gi for t in TIERS if t in widest for gi in widest[t][1]["final_companies"]}
    listed |= {gi for _, gi in prov[:final_n]}

    # All Companies: every company, ranked within its size tier
    in_a = set(cand_a)

    def stage_of(gi, w):
        min_align = tier_min_alignment(cfg, tier[gi])
        if gi in excluded:
            return f"Left out ({companies[gi].get('company_status')})"
        if w is None:
            return "Not scored"
        if gi in listed:
            return (f"Listed ({PROVIDER_TAB})" if is_provider(companies[gi])
                    else f"Listed (top {final_n})")
        if gi in w["sec"]:
            return f"Secondary scored (top {cutoff})"
        if gi in w["cand"]:
            if align.get(gi, {}).get("reason") == NO_TEXT_REASON:
                return "Not scored (no description)"
            if gi not in w["p"]["eligible"]:
                return f"Alignment below {min_align:g}" if min_align else "Alignment scored"
            return "Alignment scored"
        if not texts[gi]:
            return "Not scored (no description)"
        return "Filtered out (pre-filter)"

    def tier_order(p):
        """Ranking of every scored company in a tier: the listed ones by overall
        score, then the rest of the partnership-scored ones by overall score,
        then every other company with a 1-9 alignment score by preliminary
        score. Companies with no score (NA, no description) are not ranked."""
        listed = list(p["final"])
        rest_sec = sorted((g for g in p["secondary"] if g not in set(listed)),
                          key=lambda g: p["overall"][g], reverse=True)
        seen = set(listed) | set(rest_sec)
        scored = [g for g in p["candidates"]
                  if g not in seen and to_num(align.get(g, {}).get("score")) is not None]
        rest = sorted(scored, key=lambda g: p["prelim"][g], reverse=True)
        return {g: k for k, g in enumerate(listed + rest_sec + rest, start=1)}

    info = {t: {"rank": {gi: k for k, gi in enumerate(p["final"], start=1)},
                "order": tier_order(p),
                "sec": set(p["secondary"]), "cand": set(p["candidates"]), "p": p}
            for t, (_, p) in widest.items()}

    def sort_key(gi):
        w = info.get(tier[gi])
        order = TIERS.index(tier[gi])
        if w and gi in w["order"]:
            return (order, 0, w["order"][gi])
        return (order, 1, gi)

    all_rows = []
    for gi in sorted(range(n), key=sort_key):
        w = info.get(tier[gi])
        r = row(gi, w["order"].get(gi) if w else None)
        r["prelim_score"] = w["p"]["prelim"].get(gi) if w else None
        r["stage"] = stage_of(gi, w)
        r["in_prefilter"] = ("Yes" if gi in in_a else "No") if tier[gi] == "Small" else "All scored"
        r["found_by"] = found_by.get(gi)
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
        "n_full": "; ".join(f"{tab_title(t, widest[t][0])}: {len(widest[t][1]['candidates']):,}"
                            for t in TIERS if t in widest),
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
    ap.add_argument("--full", action="store_true",
                    help="Small companies: AI-score EVERY one instead of the 300 most "
                         "similar (hours; off by default).")
    ap.add_argument("--no-full", action="store_true",
                    help="Only the pre-filter (the default, kept for old commands).")
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
    full = (args.full or bool(cfg.prefilter.get("full_run", False))) and not args.no_full

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
