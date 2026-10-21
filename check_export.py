#!/usr/bin/env python3
"""Check data/input/companies_raw.xlsx against config.yaml before combine /
enrich: every column the pipeline needs is there, at the letter config.yaml
says, and actually filled in.

    python check_export.py                 # the configured export
    python check_export.py path/to/file.xlsx
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from nexus.capitaliq_layout import EXPECTED, fill_warnings, problems, resolve, where
from nexus.settings import Settings


def main() -> int:
    cfg = Settings()
    ciq = cfg.raw["capitaliq"]          # the letters written in config.yaml
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else cfg.companies_raw
    if not path.exists():
        print(f"Not found: {path}")
        return 1
    df = pd.read_excel(path, header=int(ciq["header_row"]) - 1, dtype=str)
    name = df.columns[0]
    df = df[df[name].notna() & ~df[name].astype(str).str.startswith("Data provided by")]
    headers = [str(c) for c in df.columns]
    print(f"\n{path.name}: {len(df):,} companies, {len(headers)} columns\n")
    found = where(headers)
    for key, (_, item, required) in EXPECTED.items():
        print(f"  {key:17} column {found[key] or 'MISSING':8}  {item}"
              f"{'' if required else ' (optional)'}")
    # columns are found by header name, so only MISSING ones are a problem
    used, _ = resolve(headers, ciq)
    p = problems(headers, used)
    w = fill_warnings(df, used) if not p else []
    print()
    for x in p:
        print(f"  PROBLEM  {x}")
    for x in w:
        print(f"  WARNING  {x}")
    if not p and not w:
        print("  OK - every column found (by name, any order) and the key columns are filled in.")
    return 1 if p else 0


if __name__ == "__main__":
    sys.exit(main())
