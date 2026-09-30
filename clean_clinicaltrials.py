#!/usr/bin/env python3
"""ClinicalTrials.gov company trials, cleaned  ->  data/cache/ctgov_clean.csv

One row per TRIAL per COMPANY on it. A trial with a company lead sponsor and
a second company as collaborator gives two rows, one for each, so every
company can be found by its own name at the combine step. No Capital IQ
here - matching happens at the combine step, like SBIR, Form D and XBRL.

What each row answers:

  university ties   partners - the universities, hospitals and foundations
                    on the trial (sponsor class OTHER), as written
                    is_active - is the trial running now? A partner on a
                    running trial is a CURRENT tie, on an ended one a PAST tie
  money now         phase and status: an active Phase 2 / 3 trial is costly
                    research happening now; start_year for the 2025+ rule
  research focus    conditions and interventions (the treatments tested)
  local presence    sites_ct / ct_sites - trial sites in Connecticut

Status -> is_active:
  running  RECRUITING, NOT_YET_RECRUITING, ACTIVE_NOT_RECRUITING,
           ENROLLING_BY_INVITATION
  ended    COMPLETED, TERMINATED, SUSPENDED, UNKNOWN (status not updated in
           years), and WITHDRAWN (stopped before anyone enrolled)
  Expanded-access programmes (AVAILABLE, APPROVED_FOR_MARKETING, ...) are
  not trials and are dropped.

No person is named anywhere: investigators and contacts are never
downloaded (see fetch_clinicaltrials.py), and a sponsor that may be an
individual doctor is left out of partners (see INSTITUTION below).

Input:  data/sources/clinicaltrials/page_*.json.gz  (python fetch_clinicaltrials.py)
Output: data/cache/ctgov_clean.csv

Usage:
    python clean_clinicaltrials.py
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

import pandas as pd

ACTIVE = {"RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING",
          "ENROLLING_BY_INVITATION"}
ENDED = {"COMPLETED", "TERMINATED", "SUSPENDED", "UNKNOWN", "WITHDRAWN"}
PHASE = {"EARLY_PHASE1": "Early Phase 1", "PHASE1": "Phase 1", "PHASE2": "Phase 2",
         "PHASE3": "Phase 3", "PHASE4": "Phase 4", "NA": "Not applicable"}

# A trial's non-company sponsor is sometimes an individual doctor rather than
# an institution (ClinicalTrials.gov still files them as "OTHER"). People's
# names must not end up in our files, and we cannot reliably tell a name
# from an organisation - so a partner is kept only if its name contains an
# institution word. Some real organisations without one are lost too
# (about 10% of partner mentions); US universities and hospitals all pass.
INSTITUTION = re.compile(
    r"univ|college|hosp|h[oô]pita|medical|medicine|health|clinic|institut|school|"
    r"cancer|center|centre|foundation|research|umc|nhs|trust|academ|scien|facult|"
    r"society|council|ministry|association|consortium|network|group|alliance|agency|"
    r"department|program|lab|charit|fondazione|fundaci|stichting|verein|e\.v\.|gmbh|"
    r"llc|inc\b|ltd|kaiser|leuven|karolinska|oncolog|cardio|policlinic|polyclinic|"
    r"sanit|salud|sant[eé]|klinik|inserm|cnrs|assistance publique|hospices|irccs|"
    r"istituto|azienda|ospedale|pharmac|biobank|trials|study|studies|ziekenhuis|spital|"
    r"sykehus|infirmary|polytechn|zentrum|organi[sz]ation|international|fund\b|union",
    re.I)

OUT_COLUMNS = [
    "company", "role", "nct_id", "title", "status", "is_active",
    "study_type", "phase", "phase_number", "start_date", "start_year",
    "completion_date", "last_update", "enrollment",
    "conditions", "interventions", "intervention_types", "partners", "other_companies",
    "sites_total", "sites_us", "sites_ct", "ct_sites",
]


def _join(values) -> str:
    seen, out = set(), []
    for v in values:
        v = (v or "").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return "; ".join(out)


def _date(struct) -> str | None:
    return (struct or {}).get("date")


def phase_of(phases) -> tuple:
    """(label, highest number). ["PHASE1", "PHASE2"] -> ("Phase 1/2", 2)."""
    phases = [p for p in (phases or []) if p in PHASE]
    if not phases:
        return None, None
    nums = [int(p[-1]) for p in phases if p[-1].isdigit()]
    if phases == ["NA"]:
        return PHASE["NA"], None
    if len(phases) == 2 and all(p.startswith("PHASE") for p in phases):
        label = "Phase " + "/".join(p[-1] for p in phases)
    else:
        label = " / ".join(PHASE[p] for p in phases)
    return label, max(nums) if nums else None


def study_rows(study: dict) -> list:
    """The rows for one trial: one per company on it."""
    ps = study.get("protocolSection") or {}
    ident = ps.get("identificationModule") or {}
    status_m = ps.get("statusModule") or {}
    sponsors = ps.get("sponsorCollaboratorsModule") or {}
    design = ps.get("designModule") or {}
    conds = ps.get("conditionsModule") or {}
    arms = ps.get("armsInterventionsModule") or {}
    locs = (ps.get("contactsLocationsModule") or {}).get("locations") or []

    status = status_m.get("overallStatus")
    if status not in ACTIVE | ENDED:
        return []                                  # expanded access etc.

    orgs = []
    lead = sponsors.get("leadSponsor") or {}
    if lead.get("name"):
        orgs.append((lead["name"].strip(), lead.get("class"), "lead sponsor"))
    for c in sponsors.get("collaborators") or []:
        if c.get("name"):
            orgs.append((c["name"].strip(), c.get("class"), "collaborator"))
    companies = [(n, r) for n, cls, r in orgs if cls == "INDUSTRY"]
    if not companies:
        return []
    partners = _join(n for n, cls, _ in orgs if cls == "OTHER" and INSTITUTION.search(n))

    label, number = phase_of(design.get("phases"))
    start = _date(status_m.get("startDateStruct"))
    us = [l for l in locs if (l.get("country") or "") == "United States"]
    ct = [l for l in us if (l.get("state") or "") == "Connecticut"]

    base = {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "status": status,
        "is_active": status in ACTIVE,
        "study_type": design.get("studyType"),
        "phase": label,
        "phase_number": number,
        "start_date": start,
        "start_year": int(start[:4]) if start and start[:4].isdigit() else None,
        "completion_date": _date(status_m.get("completionDateStruct"))
                           or _date(status_m.get("primaryCompletionDateStruct")),
        "last_update": _date(status_m.get("lastUpdatePostDateStruct")),
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "conditions": _join(conds.get("conditions") or []),
        "interventions": _join(i.get("name") for i in arms.get("interventions") or []),
        # DRUG, DEVICE, BIOLOGICAL, PROCEDURE, BEHAVIORAL, ... - the kind of
        # treatment, which says more about fit with a faculty than a drug code
        "intervention_types": _join(i.get("type") for i in arms.get("interventions") or []),
        "partners": partners,
        "sites_total": len(locs),
        "sites_us": len(us),
        "sites_ct": len(ct),
        "ct_sites": _join(f"{l.get('facility') or 'unnamed site'} ({l.get('city') or '?'})" for l in ct),
    }
    rows = []
    for name, role in companies:
        others = _join(n for n, _ in companies if n != name)
        rows.append({"company": name, "role": role, "other_companies": others, **base})
    return rows


def read_pages(src: Path):
    files = sorted(src.glob("page_*.json.gz")) + sorted(src.glob("page_*.json"))
    if not files:
        raise FileNotFoundError(f"No ClinicalTrials.gov pages in {src}\n"
                                "Run 'python fetch_clinicaltrials.py' first.")
    for f in files:
        opener = gzip.open if f.suffix == ".gz" else open
        with opener(f, "rt", encoding="utf-8") as fh:
            yield from (json.load(fh).get("studies") or [])


def clean(src: Path) -> pd.DataFrame:
    rows, seen = [], set()
    for study in read_pages(src):
        nct = ((study.get("protocolSection") or {}).get("identificationModule") or {}).get("nctId")
        if not nct or nct in seen:
            continue                           # a trial repeated across pages
        seen.add(nct)
        rows.extend(study_rows(study))
    df = pd.DataFrame(rows, columns=OUT_COLUMNS)
    df["phase_number"] = df["phase_number"].astype("Int64")
    df["start_year"] = df["start_year"].astype("Int64")
    return df.sort_values(["company", "start_date"], ascending=[True, False],
                          na_position="last").reset_index(drop=True)


def main() -> int:
    from nexus.settings import Settings
    cfg = Settings()
    src = cfg.sources_dir / "clinicaltrials"
    dst = cfg.cache_dir / "ctgov_clean.csv"
    print(f"\n=== NexusAI: clean ClinicalTrials.gov trials ===\n\n  reading {src}")
    out = clean(src)
    try:
        out.to_csv(dst, index=False, encoding="utf-8")
    except PermissionError as e:
        raise PermissionError(f"Could not write {dst.name} - is it open in "
                              "Excel? Close it and re-run.") from e
    print(f"  rows (trial x company)         {len(out):>9,}")
    print(f"  trials                         {out['nct_id'].nunique():>9,}")
    print(f"  companies (as written)         {out['company'].nunique():>9,}")
    print(f"  running now                    {out.drop_duplicates('nct_id')['is_active'].sum():>9,}")
    print(f"  with a university / hospital   {(out.drop_duplicates('nct_id')['partners'] != '').sum():>9,}")
    print(f"  with a Connecticut site        {(out.drop_duplicates('nct_id')['sites_ct'] > 0).sum():>9,}")
    print(f"\n  written: {dst}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
