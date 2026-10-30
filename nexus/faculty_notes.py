"""Per-faculty notes: data/input/faculty_notes.yaml.

A faculty member who is NOT in the file is matched exactly as before. One
who is can have any of:

  extra_research:  text added to the research profile (the AI and the
                   pre-filter search both read it)
  ignore_publications: titles (or parts of titles) to leave out of the
                   publications file - e.g. another researcher's papers
  distance_full_score_miles: companies up to this far get the full distance
                   score; past it the usual decline starts from there
  leads:           companies the researcher named. Each is always AI-scored
                   and shown on a "Researcher's leads" sheet, with its whole
                   corporate group; distance does not count against it; the
                   note (if any) is given to the justification; and its
                   description is searched for similar companies.

    "Jane Doe":
      extra_research: |
        Provider education modules on cannabis use in pregnancy ...
      distance_full_score_miles: 400
      leads:
        - name: Jazz Pharmaceuticals
          note: Offers medical education grants with a cannabinoid section.
        - Another Company Inc.            # a name alone is fine too
      ignore_publications:
        - HIV stigma

A lead that is not in this run's company list is looked for in the lists
under faculty_notes.lead_sources in config.yaml (the all-US datasets).
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from nexus.faculty_registration import name_key, same_person
from nexus.groups import group_key

DEFAULT_PATH = "data/input/faculty_notes.yaml"
FIELDS = {"extra_research", "ignore_publications", "distance_full_score_miles", "leads"}


class NotesError(ValueError):
    pass


def load_notes(path) -> Dict[str, dict]:
    """{faculty name: entry}; {} when the file does not exist. A mistake in
    the file stops the run with a plain message rather than being ignored."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise NotesError(f"{path.name} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise NotesError(f"{path.name}: expected faculty names at the top level")
    out = {}
    for name, entry in data.items():
        entry = entry or {}
        if not isinstance(entry, dict):
            raise NotesError(f"{path.name}: the entry for {name!r} must be a set of fields")
        unknown = set(entry) - FIELDS
        if unknown:
            raise NotesError(f"{path.name}: unknown field(s) for {name!r}: "
                             f"{', '.join(sorted(unknown))} (allowed: {', '.join(sorted(FIELDS))})")
        miles = entry.get("distance_full_score_miles")
        if miles is not None and (not isinstance(miles, (int, float)) or miles < 0):
            raise NotesError(f"{path.name}: distance_full_score_miles for {name!r} "
                             "must be a number of miles")
        out[str(name)] = entry
    return out


def notes_for(notes: Dict[str, dict], faculty_name: str) -> Optional[dict]:
    """The entry for this faculty: same name (accents, case and punctuation
    ignored), else the same first and last name."""
    if not notes:
        return None
    for name, entry in notes.items():
        if name_key(name) == name_key(faculty_name):
            return entry
    hits = [e for n, e in notes.items() if same_person(n, faculty_name)]
    return hits[0] if len(hits) == 1 else None


def leads_of(entry: Optional[dict]) -> List[tuple]:
    """[(lead name, note or None)]"""
    out = []
    for item in (entry or {}).get("leads") or []:
        if isinstance(item, dict):
            name, note = item.get("name"), item.get("note")
        else:
            name, note = item, None
        name = str(name or "").strip()
        if name:
            out.append((name, str(note).strip() if note else None))
    return out


def ignore_terms(entry: Optional[dict]) -> List[str]:
    return [str(t).strip().lower() for t in (entry or {}).get("ignore_publications") or []
            if str(t).strip()]


def keep_publication(title: str, terms: List[str]) -> bool:
    t = str(title or "").lower()
    return not any(term in t for term in terms)


def plateau_distance_score(miles, decay: float, full_within: float) -> float:
    """Full score (1.0) up to `full_within` miles, then the usual
    exp(-miles/decay) decline counted from there. Unknown distance -> 0, as
    in scoring.distance_score."""
    try:
        m = float(miles)
    except (TypeError, ValueError):
        return 0.0
    if m != m or m < 0:
        return 0.0
    return math.exp(-max(0.0, m - float(full_within)) / float(decay))


def _words(name) -> List[str]:
    return group_key(name).split()


def matches_lead(company: dict, lead: str) -> bool:
    """The company's name - or the parent it names - starts with the lead's
    name (legal form, ticker and plurals ignored): "Jazz Pharmaceuticals"
    finds "Jazz Pharmaceuticals plc (NasdaqGS:JAZZ)" and "Jazz
    Pharmaceuticals (EUSA Pharma USA) Inc."."""
    want = _words(lead)
    if not want:
        return False
    for field in ("company", "parent_company"):
        have = _words(company.get(field))
        if have[:len(want)] == want:
            return True
    return False


def lead_members(lead: str, companies: List[dict], groups: List[Optional[int]]) -> List[int]:
    """Every company of the lead's corporate group in this list."""
    hits = {i for i, c in enumerate(companies) if matches_lead(c, lead)}
    group_ids = {groups[i] for i in hits if groups[i] is not None}
    hits |= {i for i, g in enumerate(groups) if g is not None and g in group_ids}
    return sorted(hits)


def summary(entry: dict, found: Dict[str, List[str]], extra_from: Dict[str, str]) -> str:
    """One line for Run Info: what was customised for this faculty."""
    parts = []
    if entry.get("extra_research"):
        parts.append("extra research text added to the profile")
    terms = ignore_terms(entry)
    if terms:
        parts.append(f"publications left out when the title contains: {', '.join(terms)}")
    if entry.get("distance_full_score_miles") is not None:
        parts.append(f"full distance score up to {entry['distance_full_score_miles']:g} miles")
    for lead, note in leads_of(entry):
        names = found.get(lead) or []
        src = f" (from {extra_from[lead]})" if lead in extra_from else ""
        parts.append(f"lead '{lead}': " + (f"{len(names)} compan{'y' if len(names) == 1 else 'ies'}"
                                          f"{src} - distance not counted"
                                          if names else "not found in any company list"))
    return "; ".join(parts) if parts else "entry present, nothing set"
