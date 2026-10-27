"""The BD-IFR Faculty Registration form export (CSV) as a faculty source.

The form asks far more than the old Faculty Database (one research
paragraph and two industry labels): keywords, a problem statement, the
nature of the research, primary and secondary industry sector, and free
text about industry interests. All of it describes the RESEARCH and is
used for matching. Contact details (emails), submitter IDs, years in post
and workflow fields are never read.

Rows are numbered as in a spreadsheet: the header is row 1, so the first
faculty is row 2 ("row 13" = the 12th faculty).
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import List

import pandas as pd

# our field -> the start of the form question (the headers are long, and
# the form tool may reword the tail of a question)
FIELDS = {
    "name": "Name",
    "school": "School/ College",
    "department": "Department",
    "nature": "Which of the following best describes the nature of your research",
    "nature_other": "You selected “Other.” Please briefly describe the nature of your research",
    "keywords": "Please provide 3",
    "research": "Please briefly describe your research",
    "industry_experience": "How would you describe your previous experience working with industry",
    "partner_in_mind": "Do you currently have a specific company",
    "sector1": "Which industry sector is most relevant",
    "sector2": "Which secondary industry sector is relevant",
    "notes": "Is there anything else about your research",
}
EXACT = {"name", "department"}      # short headers: must match exactly


def name_key(name) -> str:
    """A person's name for matching: accents, case, spaces and punctuation
    ignored - "Cristina Colón-Semenza" = "cristina colon semenza"."""
    s = unicodedata.normalize("NFKD", "" if name is None else str(name))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _columns(headers) -> dict:
    found = {}
    for field, question in FIELDS.items():
        q = _norm(question)
        for h in headers:
            hn = _norm(h)
            if (hn == q) if field in EXACT else hn.startswith(q):
                found[field] = h
                break
    return found


def _text(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def read_registration(path: Path) -> List[dict]:
    """One dict per faculty, research fields only."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Faculty registration file not found at {path}. "
            "Place it there or update paths.faculty_registration in config.yaml.")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    cols = _columns(df.columns)
    if "name" not in cols:
        raise ValueError(f"{path.name}: no 'Name' column - is this the registration export?")
    out = []
    for i, row in df.iterrows():
        rec = {f: _text(row[c]) for f, c in cols.items()}
        if not rec.get("name"):
            continue
        rec["row"] = i + 2                       # spreadsheet row (header = 1)
        rec["source"] = "registration"
        # the keys the rest of the pipeline already knows
        rec["class1"], rec["class2"] = rec.get("sector1", ""), rec.get("sector2", "")
        rec["flag"] = ""
        out.append(rec)
    return out


def research_profile(fac: dict) -> str:
    """The text that represents a faculty member in matching: embedded for
    the pre-filter and read by the AI scorers. Every line is labelled so
    the model can weigh keywords, problem statement and sectors separately."""
    lines = []
    def add(label, key):
        v = (fac.get(key) or "").strip()
        if v:
            lines.append(f"{label}: {v}")
    add("Research keywords", "keywords")
    add("Research description", "research")
    nature = ", ".join(x for x in (fac.get("nature"), fac.get("nature_other")) if x)
    if nature:
        lines.append(f"Nature of research: {nature}")
    add("Primary industry sector", "sector1")
    add("Secondary industry sector", "sector2")
    add("Industry interests and goals", "notes")
    dept = ", ".join(x for x in (fac.get("department"), fac.get("school")) if x)
    if dept:
        lines.append(f"Department: {dept}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Sector match: the form's sectors and Capital IQ's "Industry
# Classifications" are the same GICS industry groups, worded slightly
# differently ("Health Care: Equipment and Services" vs "Health Care
# Equipment and Services (Primary)").
# ----------------------------------------------------------------------

_STOP = {"and", "the", "of", "primary"}


def _tokens(s: str) -> set:
    return {t for t in re.findall(r"[a-z]+", s.lower()) if t not in _STOP}


def _sector_in(sector: str, industry: str) -> bool:
    if not sector or not industry:
        return False
    # "Health Care: Equipment and Services" - the part after the colon names
    # the industry group; the part before is the broad sector
    group = sector.split(":", 1)[1] if ":" in sector else sector
    need = _tokens(group)
    return bool(need) and need <= _tokens(industry)


def sector_match(industry, fac: dict) -> str:
    """'Primary sector' / 'Secondary sector' / '' for one company."""
    industry = _text(industry)
    if _sector_in(fac.get("sector1") or fac.get("class1") or "", industry):
        return "Primary sector"
    if _sector_in(fac.get("sector2") or fac.get("class2") or "", industry):
        return "Secondary sector"
    return ""
