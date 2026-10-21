"""Check a Capital IQ export against the column letters in config.yaml
before anything reads it - a re-screened export can drop or reorder
columns, and the pipeline addresses them BY LETTER.

  problems(headers, ciq)      header row vs config: missing / moved columns
  fill_warnings(df, ciq)      columns that exist but are (nearly) empty -
                              e.g. "Revenues [LTM]" instead of "Total Revenue"
  report(path, ciq)           both, plus where each wanted column actually is

Run it on its own:  python check_export.py
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd
from openpyxl.utils import column_index_from_string, get_column_letter

# config key -> (words the header must contain, data item to export, required)
EXPECTED = {
    "col_company": ("company name", "Company Name", True),
    "col_ticker": ("ticker", "Exchange:Ticker", False),
    "col_industry": ("industry classifications", "Industry Classifications", True),
    "col_company_type": ("company type", "Company Type", True),
    "col_employees": ("employees", "Total Employees [Latest Annual]", True),
    "col_revenue": ("revenue", "Total Revenue [LTM] ($USDmm, Historical rate)", True),
    "col_address": ("offices", "Offices", True),
    "col_description": ("business description", "Business Description", True),
    "col_website": ("website", "Website", True),
    "col_status": ("company status", "Company Status", True),
}
# share of rows that should have a value, below which the column is suspect
MIN_FILL = {"col_description": 0.5, "col_revenue": 0.1, "col_employees": 0.01,
            "col_address": 0.5, "col_status": 0.9}


def _h(x) -> str:
    return " ".join(str(x or "").split()).lower()


def problems(headers: List[str], ciq: dict) -> List[str]:
    out = []
    for key, (words, item, required) in EXPECTED.items():
        letter = ciq.get(key)
        if not letter:
            if required:
                out.append(f"{key} is not set in config.yaml (needs '{item}')")
            continue
        i = column_index_from_string(str(letter)) - 1
        if i >= len(headers):
            out.append(f"{key}: column {letter} does not exist - the export has only "
                       f"{len(headers)} columns (needs '{item}')")
        elif words not in _h(headers[i]):
            out.append(f"{key}: column {letter} is '{headers[i]}', expected '{item}'")
    return out


def where(headers: List[str]) -> dict:
    """config key -> the letter where its column actually is (or None)."""
    found = {}
    for key, (words, _, _) in EXPECTED.items():
        found[key] = next((get_column_letter(i + 1) for i, h in enumerate(headers)
                           if words in _h(h)), None)
    return found


def fill_warnings(df: pd.DataFrame, ciq: dict) -> List[str]:
    out = []
    cols = list(df.columns)
    n = max(1, len(df))
    for key, need in MIN_FILL.items():
        letter = ciq.get(key)
        if not letter:
            continue
        i = column_index_from_string(str(letter)) - 1
        if i >= len(cols):
            continue
        v = df[cols[i]].astype(str).str.strip()
        share = (~v.isin(["", "-", "nan", "None", "NA", "N/A"])).sum() / n
        if share < need:
            out.append(f"{key} ('{cols[i]}') has a value for only {share:.0%} of companies "
                       f"(expected at least {need:.0%}) - export '{EXPECTED[key][1]}'")
    return out


def check_or_exit(headers: List[str], ciq: dict, path) -> None:
    """Stop with a readable message instead of an IndexError deep inside."""
    p = problems(headers, ciq)
    if not p:
        return
    found = where(headers)
    fix = [f"  {k}: {found[k]}" for k in EXPECTED if found[k] and found[k] != ciq.get(k)]
    missing = [EXPECTED[k][1] for k in EXPECTED if EXPECTED[k][2] and not found[k]]
    msg = [f"\nThe Capital IQ export does not match config.yaml: {path}"]
    msg += [f"  - {x}" for x in p]
    if missing:
        msg.append("Re-export with these data items: " + ", ".join(missing))
    if fix:
        msg.append("Columns that are in the export but at other letters - set in config.yaml "
                   "under capitaliq:")
        msg += fix
    msg.append("Check any export with:  python check_export.py")
    raise SystemExit("\n".join(msg))
