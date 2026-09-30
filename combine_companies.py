#!/usr/bin/env python3
"""Capital IQ companies + source data  ->  data/companies_combined.xlsx

The step BETWEEN the raw export and enrichment. It attaches facts from
outside sources to each Capital IQ company, and computes no scores at all -
so the matching can be checked on its own, before anything is scored.

  companies_raw.xlsx  --+
                        +-->  companies_combined.xlsx  -->  enrich  -->  companies_enriched.xlsx
  sbir_clean.csv      --+        (facts only)                           (facts + scores)

companies_raw.xlsx is never modified. Every one of its columns is carried
across unchanged, and the source columns are added on the right.

Sources so far: SBIR, Form D, then SEC XBRL. OpenAlex joins here later.

XBRL columns (from data/cache/xbrl_clean.csv, built by clean_xbrl.py) -
public companies only, figures from their own SEC filings:

  xbrl_match_name       the company as SEC names it
  xbrl_match_by         ticker     - Capital IQ ticker -> SEC's ticker list
                                     -> CIK (SEC's company ID)
                        formd_cik  - no usable ticker, but the Form D match
                                     carries a CIK that files with SEC
                                     (companies that raised privately, then
                                     listed)
  xbrl_rd_expense_usdmm R&D spend in the latest calendar year, $ millions
  xbrl_rd_year          that year
  xbrl_revenue_usdmm    revenue in the latest calendar year, $ millions
  xbrl_revenue_year     that year
  xbrl_cash_usdmm       cash on the latest balance sheet, $ millions
  xbrl_short_term_investments_usdmm
                        securities it can sell within a year, same balance
                        sheet, $ millions. Blank = none reported.
  xbrl_cash_as_of       the date of that balance sheet (a quarter-end)

No name matching at all: a ticker or a CIK belongs to exactly one company.

FORM D columns (from data/cache/formd_clean.csv, built by clean_formd.py):

  formd_match_name      the Form D company attached, as SEC spells it
  formd_match_by        name | ambiguous
  formd_raised          Yes if it has raised private money in the filings
                        we hold (2023 onward); No if not
  formd_recent_usdmm    money raised on or after combine.formd_recent_from,
                        $ millions
                        in config.yaml (currently 2025-01-01)
  formd_last_year       year of the latest filing that brought in new money
  formd_industry_group  industry the company gave SEC, most recent first

Form D matching is by NAME only - it has no website column - with the
state used to choose between two companies of the same name and to confirm
a name that is too short to trust on its own.

SBIR matching - name first, website as fallback:

  * NAME. The Capital IQ name and the SBIR name normalise to the same key.
    If two different SBIR companies share that name (e.g. ADELPHI
    TECHNOLOGY in Kentucky and in California), the website or state
    decides; if neither does, nothing is attached and the row is marked
    "ambiguous" with both candidates named, so it can be settled by eye.

  * WEBSITE. When no name matches, the Capital IQ website domain is looked
    up among SBIR websites. Only a domain belonging to exactly one SBIR
    company counts. This is what catches rebrands, where the name changed
    but the business did not.

A name match is rejected only when the websites actively disagree, or the
name is too generic to mean anything on its own - the shared rule in
nexus/matching.py. A different STATE never rejects a match: companies move,
and SBIR freezes the address as at each award.

Columns added:

  sbir_match_name      the SBIR company attached, as SBIR spells it
  sbir_match_by        name | name+suffix | website | ambiguous
                       (name+suffix: names match, websites differ, legal
                       suffix agrees - e.g. a subsidiary listed under its
                       parent's website)
  sbir_awarded         Yes if the company has ever had an SBIR/STTR award,
                       No if not, blank if the match was ambiguous
  sbir_active_usdmm    sum of awards whose contract is still running, $ millions
  sbir_active_until    latest end date among active awards
  sbir_agencies        funding agencies with award counts, most first
  university_current   research partners on ACTIVE awards
  university_past      research partners on ENDED awards, minus current
  employees_source     beside the employees column: "Capital IQ", or
                       "SBIR (year)" where Capital IQ had no figure and the
                       SBIR headcount from that year's award was used

The one change to a Capital IQ column: a BLANK employee count is filled
from SBIR. A figure Capital IQ already has is never touched.

Input:  data/input/companies_raw.xlsx, data/cache/sbir_clean.csv
Output: data/companies_combined.xlsx

Usage:
    python clean_sbir.py            # first, whenever award_data.csv changes
    python combine_companies.py
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
from openpyxl.utils import column_index_from_string

from nexus.matching import (is_too_generic, legal_suffix, match_confidence,
                            normalise_company_name, state_code,
                            state_from_address, website_domain)
from nexus.settings import Settings

SBIR_COLUMNS = [
    "sbir_match_name", "sbir_match_by", "sbir_awarded",
    "sbir_active_usdmm", "sbir_active_until",
    "sbir_agencies", "university_current", "university_past",
]

FORMD_COLUMNS = [
    "formd_match_name", "formd_match_by", "formd_raised",
    "formd_recent_usdmm", "formd_last_year", "formd_industry_group",
]

XBRL_COLUMNS = [
    "xbrl_match_name", "xbrl_match_by",
    "xbrl_rd_expense_usdmm", "xbrl_rd_year",
    "xbrl_revenue_usdmm", "xbrl_revenue_year",
    "xbrl_cash_usdmm", "xbrl_short_term_investments_usdmm", "xbrl_cash_as_of",
]

FORMD_READ = ["company", "cik", "state", "filing_number", "filing_date",
              "first_sale_date", "first_sale_yet_to_occur",
              "total_raised_to_date", "raised_since_last_filing",
              "industry_group", "is_business_combination"]

# The per-award text is deliberately NOT read here. Titles and abstracts
# stay in sbir_clean.csv until the matching step decides how to use them.
SBIR_READ = ["company", "uei", "agency", "award_amount", "contract_end_date",
             "award_active_today", "ri_name", "company_website", "state",
             "number_employees", "award_year"]


# ----------------------------------------------------------------------
# Capital IQ side
# ----------------------------------------------------------------------

def read_raw(cfg) -> tuple:
    """The Capital IQ export with EVERY column, unchanged, plus the names
    of the columns we need to match on."""
    ciq = cfg.capitaliq
    df = pd.read_excel(cfg.companies_raw, header=int(ciq["header_row"]) - 1,
                       dtype=str)
    cols = list(df.columns)

    def col(letter_key):
        return cols[column_index_from_string(str(ciq[letter_key])) - 1]

    name, site, addr = col("col_company"), col("col_website"), col("col_address")
    emp = col("col_employees")
    # Drop the trailing blank and "Data provided by Capital IQ" rows.
    df = df[df[name].notna() & (df[name].str.strip() != "")]
    df = df[~df[name].str.startswith("Data provided by", na=False)]
    return df.reset_index(drop=True), name, site, addr, emp


# ----------------------------------------------------------------------
# SBIR side: one summary per SBIR company
# ----------------------------------------------------------------------

def _distinct(values) -> list:
    seen, out = set(), []
    for v in values:
        if isinstance(v, str) and v.strip() and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def summarise_sbir(path: Path) -> dict:
    """key -> summary. Key is the UEI, or the normalised name for older
    companies that never had one."""
    a = pd.read_csv(path, dtype=str, usecols=SBIR_READ, low_memory=False)
    a["award_amount"] = pd.to_numeric(a["award_amount"], errors="coerce")
    a["active"] = a["award_active_today"].str.lower() == "true"
    a["contract_end_date"] = pd.to_datetime(a["contract_end_date"],
                                            errors="coerce")
    a["number_employees"] = pd.to_numeric(a["number_employees"], errors="coerce")
    a["award_year"] = pd.to_numeric(a["award_year"], errors="coerce")
    a["norm"] = a["company"].map(normalise_company_name)
    a["key"] = a["uei"].where(a["uei"].fillna("") != "", "NAME:" + a["norm"])
    # sbir_clean.csv is already newest-first within each company

    out = {}
    for key, g in a.groupby("key", sort=False):
        act = g[g["active"]]
        current = _distinct(act["ri_name"])
        past = [x for x in _distinct(g.loc[~g["active"], "ri_name"])
                if x not in current]
        agencies = Counter(x for x in g["agency"] if isinstance(x, str) and x)
        # headcount from the MOST RECENT award that reports one (rows are
        # newest first); zero is a blank, not a company with no staff
        emp = g[g["number_employees"] > 0]
        out[key] = {
            "name": g["company"].iloc[0],
            "norm": g["norm"].iloc[0],
            "states": {s for s in g["state"].map(state_code) if s},
            "domains": {d for d in g["company_website"].map(website_domain) if d},
            # $ millions to 3 decimals (nearest $1,000), like Capital IQ revenue ($USDmm)
            "sbir_active_usdmm": round(act["award_amount"].sum() / 1e6, 3) if len(act) else None,
            # a plain ISO string, so Excel shows 2026-12-31, not a datetime
            "sbir_active_until": act["contract_end_date"].max().strftime("%Y-%m-%d") if len(act) else None,
            "sbir_agencies": "; ".join(f"{n} ({c})" for n, c in agencies.most_common()),
            "university_current": "; ".join(current),
            "university_past": "; ".join(past),
            "employees": int(emp["number_employees"].iloc[0]) if len(emp) else None,
            "employees_year": int(emp["award_year"].iloc[0])
                              if len(emp) and pd.notna(emp["award_year"].iloc[0]) else None,
        }
    return out


def build_indexes(sbir: dict) -> tuple:
    by_name = defaultdict(list)
    by_domain = defaultdict(set)
    for key, s in sbir.items():
        if s["norm"]:
            by_name[s["norm"]].append(key)
        for d in s["domains"]:
            by_domain[d].add(key)
    # a domain shared by several SBIR companies identifies none of them
    by_domain = {d: next(iter(k)) for d, k in by_domain.items() if len(k) == 1}
    return by_name, by_domain


# ----------------------------------------------------------------------
# Form D side: one summary per company (by CIK, SEC's company ID)
# ----------------------------------------------------------------------

def summarise_formd(path: Path, recent_from) -> dict:
    """cik -> summary.

    Money is only counted from filings that are real fundraising: merger
    filings and "first sale yet to occur" filings are skipped.

    RECENT money is money that came in on or after `recent_from`:
      * an UPDATE (D/A) filed on or after that date -> its
        raised_since_last_filing, the money raised since the previous filing
      * a FIRST filing (D) whose FIRST SALE is on or after that date -> all
        of it. Judged by first sale, not filing date, because some companies
        file years late: Agilix filed in 2024 for a raise begun in 2016.

    Form D money, unlike SBIR, is free cash - investors' money the company
    can spend as it chooses, including on sponsored research.
    """
    f = pd.read_csv(path, dtype={"cik": str}, usecols=FORMD_READ,
                    low_memory=False)
    f["filing_date"] = pd.to_datetime(f["filing_date"], errors="coerce")
    f["first_sale_date"] = pd.to_datetime(f["first_sale_date"], errors="coerce")
    for col in ("first_sale_yet_to_occur", "is_business_combination"):
        f[col] = f[col].astype(str).str.lower() == "true"
    f["norm"] = f["company"].map(normalise_company_name)
    start = pd.Timestamp(recent_from)

    real = ~f["is_business_combination"] & ~f["first_sale_yet_to_occur"]
    is_update = f["filing_number"] > 1
    in_window = (is_update & (f["filing_date"] >= start)) | \
                (~is_update & (f["first_sale_date"] >= start))
    f["recent"] = f["raised_since_last_filing"].where(real & in_window)
    f["new_money"] = real & (f["raised_since_last_filing"] > 0)
    f = f.sort_values("filing_date", ascending=False)

    out = {}
    for cik, g in f.groupby("cik", sort=False):
        money = g[g["new_money"]]
        recent = g["recent"].where(g["recent"] > 0).sum()
        industries = list(dict.fromkeys(
            x for x in g["industry_group"] if isinstance(x, str) and x))
        out[cik] = {
            "name": g["company"].iloc[0],
            "norm": g["norm"].iloc[0],
            "states": {s for s in g["state"].map(state_code) if s},
            # ever raised real money in the filings we hold (2023 onward)
            "formd_raised": "Yes" if (real[g.index] &
                                      (g["total_raised_to_date"] > 0)).any() else "No",
            # $ millions to 3 decimals (nearest $1,000), like Capital IQ revenue ($USDmm)
            "formd_recent_usdmm": round(recent / 1e6, 3) if recent > 0 else None,
            "formd_last_year": int(money["filing_date"].iloc[0].year)
                               if len(money) else None,
            "formd_industry_group": "; ".join(industries) or None,
        }
    return out


def match_formd(name, address, formd, by_name) -> tuple:
    """(cik or None, how, note). Name only - Form D has no website - with
    the state used to pick between namesakes and to confirm a short name."""
    key = normalise_company_name(name)
    our_state = state_from_address(address)
    cands = by_name.get(key, []) if key else []
    if len(cands) > 1:
        by_state = [k for k in cands if our_state and our_state in formd[k]["states"]]
        if len(by_state) != 1:
            return None, "ambiguous", " / ".join(sorted({formd[k]["name"] for k in cands}))
        cands = by_state
    if len(cands) == 1:
        f = formd[cands[0]]
        if match_confidence(key, our_state, f["states"]) == "suspect":
            return None, "rejected", f["name"]
        return cands[0], "name", None
    return None, None, None


# ----------------------------------------------------------------------
# XBRL side: one summary per public company (by CIK)
# ----------------------------------------------------------------------

def _usdmm(v):
    """Dollars -> $ millions to 3 decimals, like Capital IQ's revenue."""
    return None if v is None or v != v else round(float(v) / 1e6, 3)


def summarise_xbrl(path: Path) -> dict:
    """cik -> latest figures.

    R&D and revenue: the latest calendar year the company reported.
    Cash and short-term investments: the latest balance sheet, and BOTH
    from that same date - mixing a June cash figure with a March
    investments figure would describe a moment that never existed.
    """
    x = pd.read_csv(path, usecols=["company", "cik", "concept", "period",
                                   "period_type", "period_end", "value_usd"])
    x = x.sort_values("period_end")
    out = {}
    for cik, g in x.groupby("cik", sort=False):
        s = {"name": g["company"].iloc[-1]}
        for concept, col in (("rd_expense", "xbrl_rd"), ("revenue", "xbrl_revenue")):
            y = g[(g["concept"] == concept) & (g["period_type"] == "year")]
            if len(y):
                last = y.iloc[-1]
                s[f"{col}_usdmm" if concept == "revenue" else "xbrl_rd_expense_usdmm"] = _usdmm(last["value_usd"])
                s[f"{col}_year"] = int(str(last["period"])[2:6])
        bs = g[g["concept"].isin(["cash", "short_term_investments"])
               & (g["period_type"] == "instant")]
        if len(bs):
            latest = bs["period_end"].max()
            day = bs[bs["period_end"] == latest]
            for concept, col in (("cash", "xbrl_cash_usdmm"),
                                 ("short_term_investments",
                                  "xbrl_short_term_investments_usdmm")):
                v = day.loc[day["concept"] == concept, "value_usd"]
                s[col] = _usdmm(v.iloc[0]) if len(v) else None
            s["xbrl_cash_as_of"] = latest
        out[int(cik)] = s
    return out


def load_ticker_ciks(path: Path) -> dict:
    """ticker -> CIK from SEC's list. Every ticker a company has (common,
    preferred, warrants) points to it, so any of them resolves."""
    if not path.exists():
        return {}
    out = {}
    for row in pd.read_json(path, orient="index").itertuples():
        out[str(row.ticker).upper()] = int(row.cik_str)
    return out


def ticker_of(value):
    """Capital IQ writes 'NasdaqCM:ACET' or '-'. Return the bare symbol."""
    s = "" if value is None or value != value else str(value).strip()
    if not s or s == "-" or s.lower() == "nan":
        return None
    return (s.split(":")[-1] if ":" in s else s).strip().upper() or None


def match_xbrl(ticker, formd_cik, xbrl, ticker_ciks) -> tuple:
    """(cik or None, how). Ticker first; the Form D CIK only when the
    ticker gives nothing."""
    t = ticker_of(ticker)
    cik = ticker_ciks.get(t) if t else None
    if cik in xbrl:
        return cik, "ticker"
    if formd_cik is not None:
        try:
            fc = int(formd_cik)
        except (TypeError, ValueError):
            fc = None
        if fc in xbrl:
            return fc, "formd_cik"
    return None, None


# ----------------------------------------------------------------------
# The join
# ----------------------------------------------------------------------

def match_one(name, website, address, sbir, by_name, by_domain) -> tuple:
    """(sbir_key or None, how, note)."""
    key = normalise_company_name(name)
    our_domain = website_domain(website)
    our_state = state_from_address(address)

    cands = by_name.get(key, []) if key else []
    if len(cands) > 1:
        # Same name, different companies. Let the website, then the state,
        # decide - and only if it picks out exactly one.
        by_site = [k for k in cands if our_domain and our_domain in sbir[k]["domains"]]
        by_state = [k for k in cands if our_state and our_state in sbir[k]["states"]]
        pick = by_site if len(by_site) == 1 else by_state if len(by_state) == 1 else []
        if len(pick) != 1:
            names = " / ".join(sorted({sbir[k]["name"] for k in cands}))
            return None, "ambiguous", names
        cands = pick

    if len(cands) == 1:
        s = sbir[cands[0]]
        conf = match_confidence(key, our_state, s["states"], our_domain, s["domains"])
        if conf == "suspect":
            # The websites disagree - normally a sign of two different
            # companies sharing a name (Casimir, LLC at casimirtrials.com vs
            # CASIMIR, INC at casimirspace.com). But Capital IQ often lists a
            # subsidiary under its PARENT's website: NeuroFlow, Inc. shows
            # quartethealth.com. A matching legal suffix breaks the tie.
            #
            # It never overrides the too-generic rule: "ACT Group, Inc." and
            # "Act Inc" share a suffix and are still not safe to join.
            websites_differ = bool(our_domain and s["domains"]
                                   and our_domain not in s["domains"])
            our_sfx, their_sfx = legal_suffix(name), legal_suffix(s["name"])
            if (websites_differ and not is_too_generic(key)
                    and our_sfx and our_sfx == their_sfx):
                return cands[0], "name+suffix", None
            return None, "rejected", s["name"]
        return cands[0], "name", None

    if our_domain and our_domain in by_domain:
        return by_domain[our_domain], "website", None
    return None, None, None


def main() -> int:
    cfg = Settings()
    sbir_path = cfg.cache_dir / "sbir_clean.csv"
    out_path = cfg.path("companies_combined", "data/companies_combined.xlsx")

    print("\n=== NexusAI: combine companies + sources ===\n")
    if not sbir_path.exists():
        print(f"  {sbir_path} not found - run 'python clean_sbir.py' first.")
        return 1

    raw, name_col, site_col, addr_col, emp_col = read_raw(cfg)
    print(f"  Capital IQ companies   {len(raw):>7,}")
    sbir = summarise_sbir(sbir_path)
    print(f"  SBIR companies         {len(sbir):>7,}")
    by_name, by_domain = build_indexes(sbir)

    added = {c: [] for c in SBIR_COLUMNS}
    how_counts = Counter()
    matched_keys = []
    for _, row in raw.iterrows():
        k, how, note = match_one(row[name_col], row[site_col], row[addr_col],
                                 sbir, by_name, by_domain)
        how_counts[how] += 1
        matched_keys.append(k)
        if k is None:
            for c in SBIR_COLUMNS:
                added[c].append(None)
            if how == "ambiguous":
                added["sbir_match_by"][-1] = "ambiguous"
                added["sbir_match_name"][-1] = note
                # it may well have awards - we just cannot say whose
            else:
                added["sbir_awarded"][-1] = "No"
            continue
        s = sbir[k]
        added["sbir_match_name"].append(s["name"])
        added["sbir_match_by"].append(how)
        # A federal R&D award is independent evidence that a small company
        # does real research - often the only such evidence for companies
        # Capital IQ has no revenue for.
        added["sbir_awarded"].append("Yes")
        for c in SBIR_COLUMNS[3:]:
            added[c].append(s[c] or None)

    # ---- Form D ----
    formd_path = cfg.cache_dir / "formd_clean.csv"
    fd_added = {c: [] for c in FORMD_COLUMNS}
    fd_counts = Counter()
    fd_ciks = [None] * len(raw)
    if formd_path.exists():
        recent_from = (cfg.raw.get("combine") or {}).get("formd_recent_from",
                                                          "2025-01-01")
        formd = summarise_formd(formd_path, recent_from)
        print(f"  Form D companies       {len(formd):>7,}   (recent money from {recent_from})")
        fd_by_name = defaultdict(list)
        for k, v in formd.items():
            if v["norm"]:
                fd_by_name[v["norm"]].append(k)
        for i, row in raw.iterrows():
            k, how, note = match_formd(row[name_col], row[addr_col], formd, fd_by_name)
            fd_counts[how] += 1
            fd_ciks[i] = k
            if k is None:
                for c in FORMD_COLUMNS:
                    fd_added[c].append(None)
                if how == "ambiguous":
                    fd_added["formd_match_by"][-1] = "ambiguous"
                    fd_added["formd_match_name"][-1] = note
                else:
                    fd_added["formd_raised"][-1] = "No"
                continue
            f = formd[k]
            fd_added["formd_match_name"].append(f["name"])
            fd_added["formd_match_by"].append(how)
            for c in FORMD_COLUMNS[2:]:
                fd_added[c].append(f[c])
    else:
        print(f"  {formd_path.name} not found - run 'python clean_formd.py'; Form D columns left blank")
        fd_added = {c: [None] * len(raw) for c in FORMD_COLUMNS}

    # ---- XBRL ----
    xbrl_path = cfg.cache_dir / "xbrl_clean.csv"
    xb_added = {c: [None] * len(raw) for c in XBRL_COLUMNS}
    xb_counts = Counter()
    if xbrl_path.exists():
        xbrl = summarise_xbrl(xbrl_path)
        ticker_ciks = load_ticker_ciks(cfg.sources_dir / "xbrl" / "company_tickers.json")
        print(f"  XBRL companies         {len(xbrl):>7,}")
        ciq = cfg.capitaliq
        tick_col = (list(raw.columns)[column_index_from_string(str(ciq["col_ticker"])) - 1]
                    if ciq.get("col_ticker") else None)
        for i, row in raw.iterrows():
            cik, how = match_xbrl(row[tick_col] if tick_col else None,
                                  fd_ciks[i], xbrl, ticker_ciks)
            xb_counts[how] += 1
            if cik is None:
                continue
            x = xbrl[cik]
            xb_added["xbrl_match_name"][i] = x["name"]
            xb_added["xbrl_match_by"][i] = how
            for c in XBRL_COLUMNS[2:]:
                xb_added[c][i] = x.get(c)
    else:
        print(f"  {xbrl_path.name} not found - run 'python clean_xbrl.py'; XBRL columns left blank")

    combined = pd.concat([raw, pd.DataFrame(added), pd.DataFrame(fd_added),
                          pd.DataFrame(xb_added)], axis=1)

    # Employees: Capital IQ first. Only where Capital IQ has NO figure is
    # the SBIR headcount used, and employees_source says which it was, so
    # a filled-in number is never mistaken for Capital IQ's own.
    def _blank(v):
        return v is None or (isinstance(v, float) and v != v) or \
            str(v).strip() in ("", "-", "nan", "0")
    ciq_has = ~combined[emp_col].map(_blank)
    sbir_emp = [sbir[k]["employees"] if k else None for k in matched_keys]
    sbir_year = [sbir[k]["employees_year"] if k else None for k in matched_keys]
    source = []
    for i in range(len(combined)):
        if ciq_has.iat[i]:
            source.append("Capital IQ")
        elif sbir_emp[i]:
            combined.at[i, emp_col] = str(sbir_emp[i])
            source.append(f"SBIR ({sbir_year[i]})" if sbir_year[i] else "SBIR")
        else:
            source.append(None)
    # right beside the employees column, so the two are read together
    combined.insert(combined.columns.get_loc(emp_col) + 1,
                    "employees_source", source)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pd.ExcelWriter(out_path, engine="openpyxl") as w:
            combined.to_excel(w, index=False, sheet_name="Companies")
            w.sheets["Companies"].freeze_panes = "B2"
    except PermissionError as e:
        raise PermissionError(f"Could not write {out_path.name} - is it open "
                              "in Excel? Close it and re-run.") from e

    matched = how_counts["name"] + how_counts["name+suffix"] + how_counts["website"]
    s = combined
    print(f"\n  SBIR matched           {matched:>7,}")
    print(f"    by name              {how_counts['name']:>7,}")
    print(f"    by name+suffix       {how_counts['name+suffix']:>7,}   (websites differ, legal suffix agrees)")
    print(f"    by website           {how_counts['website']:>7,}")
    print(f"  ambiguous (by eye)     {how_counts['ambiguous']:>7,}")
    print(f"  rejected (websites disagree / too generic) {how_counts['rejected']:>4,}")
    print(f"\n  sbir_awarded = Yes     {(s['sbir_awarded'] == 'Yes').sum():>7,}")
    print(f"  with an active award   {s['sbir_active_usdmm'].notna().sum():>7,}")
    print(f"  university current     {s['university_current'].notna().sum():>7,}")
    print(f"  university past        {s['university_past'].notna().sum():>7,}")
    src = combined["employees_source"].fillna("")
    print(f"\n  employees from Capital IQ  {(src == 'Capital IQ').sum():>7,}")
    print(f"  employees filled from SBIR {src.str.startswith('SBIR').sum():>7,}")
    print(f"  employees still blank      {(src == '').sum():>7,}")
    print(f"\n  Form D matched         {fd_counts['name']:>7,}")
    print(f"  ambiguous (by eye)     {fd_counts['ambiguous']:>7,}")
    print(f"  rejected (too generic) {fd_counts['rejected']:>7,}")
    print(f"  formd_raised = Yes     {(combined['formd_raised'] == 'Yes').sum():>7,}")
    print(f"  with recent money      {combined['formd_recent_usdmm'].notna().sum():>7,}")
    xb = combined
    print(f"\n  XBRL matched           {xb_counts['ticker'] + xb_counts['formd_cik']:>7,}")
    print(f"    by ticker            {xb_counts['ticker']:>7,}")
    print(f"    by Form D CIK        {xb_counts['formd_cik']:>7,}")
    print(f"  with R&D expense       {xb['xbrl_rd_expense_usdmm'].notna().sum():>7,}")
    print(f"  with revenue           {xb['xbrl_revenue_usdmm'].notna().sum():>7,}")
    print(f"  with cash              {xb['xbrl_cash_usdmm'].notna().sum():>7,}")
    print(f"\n  written: {out_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
