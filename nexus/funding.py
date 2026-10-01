"""Join the per-source funding caches onto the company datasource.

Each source step (enrich_funding.py, enrich_sbir.py, ...) writes its own
cache keyed by normalised company name. This module reads whichever of
those exist and turns them into the columns that land in
companies_enriched.xlsx.

It only ever READS caches. If a cache is missing, its columns come out
blank and nothing breaks, so there is no required order between running a
source step and running enrichment.

Two things it deliberately does not do:

  * It does not compute a score. Turning dollars into a 0-1 number is a
    scoring decision that belongs in config.yaml and scoring.py, not baked
    into the data.

  * It does not sum lifetime totals into a capacity figure. Money awarded
    two decades ago was spent two decades ago. `funding_recent_usd` is the
    number that means "can they fund research now"; the lifetime totals are
    reference, kept so an odd figure can be checked rather than trusted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# Cache file -> the prefix its columns carry. Adding a source later means
# adding a line here and the columns it contributes below.
SOURCES = {
    "funding_formd.json": "formd",
    "funding_sbir.json": "sbir",
    "rd_xbrl.json": "xbrl",
}

#: Columns appended to every company row, in the order they appear.
FUNDING_COLUMNS = [
    # money, per source and combined
    "formd_recent_usd", "formd_total_usd", "formd_by_year", "formd_last_year",
    "sbir_recent_usd", "sbir_total_usd", "sbir_by_year",
    "funding_recent_usd", "funding_total_usd", "funding_sources",
    # federal award track record
    "sbir_awards", "sbir_first_year", "sbir_last_year",
    "sbir_phase2_awards", "sbir_agencies", "sbir_agency_industries",
    # university ties - source-neutral names, because OpenAlex will
    # contribute to these same two columns
    "university_current", "university_past", "university_partners",
    # research intensity - public companies only, and deliberately NOT part
    # of funding_*: R&D spend is money going out, not money coming in
    "rd_expense_usd", "rd_expense_year", "rd_by_year",
    # revenue as the company itself reported it, in its own audited filing.
    # Kept beside revenue_usdmm (Capital IQ's figure) rather than replacing
    # it - two independent numbers are worth more than one overwritten, and
    # a disagreement is itself informative.
    "xbrl_revenue_usd", "xbrl_revenue_year", "xbrl_revenue_by_year",
    "xbrl_revenue_tag",
    # supplementary facts the sources happen to carry
    "sbir_employees",
    # provenance, so any row can be traced back
    "formd_match", "sbir_match", "xbrl_ticker",
]


def load_caches(cache_dir: Path) -> dict:
    """Read every source cache that exists. Returns {prefix: {key: record}}."""
    out = {}
    for filename, prefix in SOURCES.items():
        path = Path(cache_dir) / filename
        if not path.exists():
            continue
        try:
            out[prefix] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt cache should not stop enrichment; the columns it
            # would have filled simply stay blank.
            continue
    return out


def _num(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def funding_columns(key: str, caches: dict) -> dict:
    """The funding columns for one company, given its normalised name key."""
    fd = (caches.get("formd") or {}).get(key) or {}
    sb = (caches.get("sbir") or {}).get(key) or {}
    xb = (caches.get("xbrl") or {}).get(key) or {}

    # Form D's "recent" is the most recent raise: an equity round is a point
    # event, and the money either arrived or it did not.
    formd_recent = _num(fd.get("last_raise_usd"))
    formd_total = _num(fd.get("total_raised_usd"))

    # SBIR's "recent" is a multi-year window, because awards are lumpy and
    # multi-year: a company can book nothing in a year while spending an
    # award granted two years earlier.
    sbir_recent = _num(sb.get("sbir_recent_usd"))
    sbir_total = _num(sb.get("sbir_total_usd"))

    sources = []
    if fd:
        sources.append("Form D")
    if sb:
        sources.append("SBIR")

    recent = (formd_recent or 0) + (sbir_recent or 0)
    total = (formd_total or 0) + (sbir_total or 0)

    past = sb.get("university_past") or ""
    current = sb.get("university_current") or ""

    return {
        "formd_recent_usd": formd_recent,
        "formd_total_usd": formd_total,
        "formd_by_year": fd.get("by_year") or None,
        # Exposed so the score can ask "is this money RECENT?" without
        # parsing the by_year string. Money raised in 2019 was spent in 2019.
        "formd_last_year": fd.get("last_raise_year"),
        "sbir_recent_usd": sbir_recent,
        "sbir_total_usd": sbir_total,
        "sbir_by_year": sb.get("sbir_by_year") or None,
        "funding_recent_usd": round(recent, 2) if recent else None,
        "funding_total_usd": round(total, 2) if total else None,
        "funding_sources": "; ".join(sources) or None,

        "sbir_awards": sb.get("sbir_awards"),
        "sbir_first_year": sb.get("sbir_first_year"),
        "sbir_last_year": sb.get("sbir_last_year"),
        "sbir_phase2_awards": sb.get("sbir_phase2_awards"),
        "sbir_agencies": sb.get("sbir_agencies") or None,
        "sbir_agency_industries": sb.get("sbir_agency_industries") or None,

        "university_current": current or None,
        "university_past": past or None,
        "university_partners": len([p for p in past.split(";") if p.strip()])
                               or None,

        "rd_expense_usd": _num(xb.get("rd_expense_usd")),
        "rd_expense_year": xb.get("rd_expense_year"),
        "rd_by_year": xb.get("rd_by_year") or None,

        "xbrl_revenue_usd": _num(xb.get("xbrl_revenue_usd")),
        "xbrl_revenue_year": xb.get("xbrl_revenue_year"),
        "xbrl_revenue_by_year": xb.get("xbrl_revenue_by_year") or None,
        "xbrl_revenue_tag": xb.get("xbrl_revenue_tag") or None,

        "sbir_employees": sb.get("sbir_employees"),

        "formd_match": fd.get("matched_entity") or None,
        "sbir_match": sb.get("sbir_matched_name") or None,
        "xbrl_ticker": xb.get("ticker") or None,
    }


def _yes(value) -> bool:
    """True for True / "True" / "Yes" / 1 (Excel and pandas spell it all
    ways). pandas reads a True/False column that has blanks as 1.0 / 0.0 /
    NaN - and str(1.0) is "1.0", which a plain text check misses."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value).strip().lower() in ("true", "yes", "1", "1.0")


def _num_or_none(value):
    return None if _is_missing(value) else float(value)


def _year_of(value):
    from nexus.scoring import _year
    if isinstance(value, str) and len(value) >= 4:
        return _year(value[:4])
    return _year(value)


def company_funding_score(company: dict, cfg: dict) -> tuple:
    """(score, reason) - is there money available NOW? - for one company
    row of the combined / enriched sheet.

    Reads the source columns combine_companies.py attached and hands them
    to nexus.scoring.money_score. The reason names the evidence, because a
    bare 0.74 is less useful than the sentence it replaced.
    """
    from nexus.scoring import money_score
    fresh = cfg["recency"]["fresh_year"]
    signals = {
        # Form D money counted from 2025 on, SBIR awards still running:
        # both are current by definition
        "formd_recent": (_num_or_none(company.get("formd_recent_usdmm")), fresh),
        "sbir_active": (_num_or_none(company.get("sbir_active_usdmm")), fresh),
        "cash_total": (_num_or_none(company.get("xbrl_cash_total_usdmm")),
                       _year_of(company.get("xbrl_cash_as_of"))),
        "rd_expense": (_num_or_none(company.get("xbrl_rd_expense_usdmm")),
                       _year_of(company.get("xbrl_rd_year"))),
    }
    last_funded = _year_of(company.get("oa_last_funded_year"))
    oa_recent = (_yes(company.get("oa_funded_university"))
                 and last_funded is not None and last_funded >= cfg["oa_funded_from"])
    revenue = _num_or_none(company.get("revenue_usdmm"))
    small = revenue is None or revenue < cfg["formd_raised_revenue_max"]
    raised_small = _yes(company.get("formd_raised")) and small

    score, best = money_score(signals, cfg, oa_funded_recent=oa_recent,
                              formd_raised_small=raised_small)

    labels = {"formd_recent": "raised ${:,.1f}m since 2025 (Form D)",
              "sbir_active": "${:,.1f}m of SBIR/STTR awards running",
              "cash_total": "${:,.0f}m cash + short-term investments",
              "rd_expense": "${:,.0f}m R&D"}
    bits = [labels[k].format(v) for k, (v, _) in signals.items() if v is not None]
    if oa_recent:
        bits.append(f"funded university research in {last_funded}")
    if not bits:
        if raised_small:
            return score, "Raised private money before (Form D), nothing recent."
        return score, "No funding record found - neutral."
    return score, "; ".join(bits) + "."


def company_academic_adjustment(company: dict, cfg: dict) -> tuple:
    """(adjustment, reason) - the academic track record that nudges the
    partnership score, from the SBIR, ClinicalTrials.gov and OpenAlex
    columns."""
    from nexus.scoring import _nonblank, academic_adjustment
    flags = {
        "oa_funded_university": _yes(company.get("oa_funded_university")),
        "oa_coauthored_university": _yes(company.get("oa_coauthored_university")),
        "sbir_university_past": _nonblank(company.get("university_past")),
        "ct_university_past": _nonblank(company.get("ct_university_past")),
        "sbir_awarded": _yes(company.get("sbir_awarded")),
        "sbir_university_current": _nonblank(company.get("university_current")),
        "ct_university_current": _nonblank(company.get("ct_university_current")),
    }
    adj, used = academic_adjustment(flags, company.get("ct_trials_active"), cfg)
    words = {
        "oa_funded_university": "paid for university research (OpenAlex)",
        "oa_coauthored_university": "co-wrote papers with universities",
        "sbir_university_past": "past SBIR/STTR university partner",
        "ct_university_past": "past clinical-trial university partner",
        "sbir_awarded": "SBIR/STTR award history",
        "sbir_university_current": "current SBIR/STTR university partner",
        "ct_university_current": "current clinical-trial university partner",
        "ct_trials_active": "many running clinical trials",
    }
    if not used:
        return 0.0, "No academic track record found."
    return adj, f"{adj:+.2f}: " + "; ".join(words[u] for u in used) + "."


def _is_missing(value) -> bool:
    """True when a numeric cell carries no usable figure.

    Note the NaN check: pandas represents a blank cell as NaN, and NaN is
    TRUTHY in Python, so `not value` silently reports every blank cell as
    populated. This counted 72 companies without revenue where the real
    figure was over 1,700.
    """
    if value is None:
        return True
    if isinstance(value, float) and value != value:      # NaN
        return True
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


def summarise(records: list) -> dict:
    """Counts for the run summary, so it is obvious what the join added."""
    n = len(records)
    with_any = sum(1 for r in records if r.get("funding_sources"))
    with_recent = sum(1 for r in records if r.get("funding_recent_usd"))
    with_uni = sum(1 for r in records if r.get("university_past"))
    with_rd = sum(1 for r in records if r.get("rd_expense_usd"))
    with_xrev = sum(1 for r in records if r.get("xbrl_revenue_usd"))
    no_rev = [r for r in records if _is_missing(r.get("revenue_usdmm"))]
    lit = sum(1 for r in no_rev if r.get("funding_sources"))
    rev_filled = sum(1 for r in no_rev if r.get("xbrl_revenue_usd"))
    return {
        "companies": n,
        "with funding from any source": with_any,
        "with recent funding": with_recent,
        "with a university partner": with_uni,
        "with R&D expense reported": with_rd,
        "with XBRL revenue": with_xrev,
        "no Capital IQ revenue": len(no_rev),
        "  of those, now have funding data": lit,
        "  of those, XBRL supplies revenue": rev_filled,
    }
