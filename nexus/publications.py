"""A faculty member's scholarly contributions (titles, type, year) as extra
evidence of what they work on.

One CSV per faculty member (or several faculty in one file) in
data/input/publications/, as exported from the university's scholarly
contributions system: NetID, Name, Department, Title, Type, Status, Year,
Description. Only Name, Title, Type and Year are read - never the NetID or
the free-text Description.

How they are used (match_faculty.py):
  * pre-filter: every title is embedded on its own; a company's score is its
    best weighted match with any title, and this search ADDS up to
    `prefilter.extra_per_search` small companies ("Found By: publications").
    Each title on its own keeps distinct topics distinct - one average of
    49 titles would be a blur.
  * the AI reads the top titles by weight as a labelled block after the
    registration answers, which come first. The block is not part of the
    profile embedding, so the 300 closest companies stay the ones closest
    to what the researcher wrote.

Weight = age weight x type weight. Recent work counts most, older work
still counts: by default the last 5 years (or no year - forthcoming) 1.0,
6-10 years 0.6, 11-15 years 0.4, older 0.25. Articles, letters and reviews
1.0, posters / presentations 0.5, errata 0 (left out). A title listed twice
counts once (a dated copy over an undated one, else the newest).
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import List, Optional

import pandas as pd

DEFAULTS = {
    "dir": "data/input/publications",
    # (up to N years old, weight); anything older gets `older`
    "age_weights": [[5, 1.0], [10, 0.6], [15, 0.4]],
    "older": 0.25,
    "no_year": 1.0,
    # matched against the start of the Type, lower case; anything else 1.0
    "type_weights": {"journal erratum": 0.0, "erratum": 0.0,
                     "poster": 0.5, "presentation": 0.5},
    "prompt_titles": 12,
}


def settings(cfg_raw: dict) -> dict:
    s = dict(DEFAULTS)
    s.update((cfg_raw or {}).get("publications") or {})
    return s


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _key(name) -> str:
    return re.sub(r"[^a-z0-9]", "", _norm(name).lower())


def _year(v) -> Optional[int]:
    try:
        y = int(float(str(v).strip()))
        return y if 1900 < y < 2200 else None
    except (TypeError, ValueError):
        return None


def age_weight(year: Optional[int], s: dict, today: Optional[int] = None) -> float:
    if year is None:
        return float(s["no_year"])
    age = (today or date.today().year) - year
    for limit, w in s["age_weights"]:
        if age <= limit:
            return float(w)
    return float(s["older"])


def type_weight(kind: str, title: str, s: dict) -> float:
    k = _norm(kind).lower()
    if _norm(title).lower().startswith("erratum"):
        return 0.0
    for start, w in s["type_weights"].items():
        if k.startswith(start) or start in k.split("/"):
            return float(w)
    return 1.0


def read_publications(path: Path, faculty_name: str, s: dict,
                      today: Optional[int] = None) -> List[dict]:
    """The faculty member's titles with weights, best first. Rows of other
    people in the same file are ignored; weight-0 rows (errata) left out."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    cols = {c.strip().lower(): c for c in df.columns}
    need = [c for c in ("name", "title") if c not in cols]
    if need:
        raise ValueError(f"{Path(path).name}: no {', '.join(need)} column")
    want = _key(faculty_name)
    best = {}
    for _, r in df.iterrows():
        if _key(r[cols["name"]]) != want:
            continue
        title = _norm(r[cols["title"]]).rstrip(".")
        if not title:
            continue
        kind = _norm(r[cols["type"]]) if "type" in cols else ""
        year = _year(r[cols["year"]]) if "year" in cols else None
        w = age_weight(year, s, today) * type_weight(kind, title, s)
        if w <= 0:
            continue
        k = title.lower()
        old = best.get(k)
        # a title listed twice counts once: a copy with a year beats one
        # without (a stray copy is not "forthcoming"), else the newest
        if old is None or (old["year"] is None and year is not None) or \
                (year is not None and old["year"] is not None and year > old["year"]):
            best[k] = {"title": title, "type": kind, "year": year, "weight": round(w, 3)}
    return sorted(best.values(), key=lambda p: (-p["weight"], -(p["year"] or 9999), p["title"]))


def find_publications(folder: Path, faculty_name: str, s: dict,
                      today: Optional[int] = None) -> List[dict]:
    """Every CSV in `folder` that lists this faculty member (by Name)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    found = {}
    for f in sorted(folder.glob("*.csv")):
        try:
            for p in read_publications(f, faculty_name, s, today):
                k = p["title"].lower()
                if k not in found or p["weight"] > found[k]["weight"]:
                    found[k] = p
        except ValueError:
            continue
    return sorted(found.values(), key=lambda p: (-p["weight"], -(p["year"] or 9999), p["title"]))


def profile_block(pubs: List[dict], n: int) -> str:
    """The titles the AI reads, labelled so the registration answers above
    come first."""
    top = pubs[:n]
    if not top:
        return ""
    lines = [f"- {p['year'] or 'forthcoming'}: {p['title']}" for p in top]
    return ("Selected publications (supporting evidence of the researcher's expertise; "
            "the keywords and description above come first; more recent work weighs "
            "more):\n" + "\n".join(lines))
