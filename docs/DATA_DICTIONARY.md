# Data dictionary — new and modified fields

Covers the two files added in the SBIR rework. Fill counts are from the
current data (21,806 Capital IQ companies; 219,590 SBIR awards) and will
change as the inputs change.

---

## 1. `data/companies_combined.xlsx`

One row per Capital IQ company. Every Capital IQ column is carried over
unchanged, **except** the employee count, which is filled from SBIR where
Capital IQ is blank. The fields below are added on the right.

Built by `combine_companies.py` from `data/input/companies_raw.xlsx` and
`data/cache/sbir_clean.csv`. No AI, no scores.

### Modified fields

| Field | Type | What changed | Example | Filled |
|---|---|---|---|---|
| Total Employees [Latest Annual] | number | Capital IQ value kept as is. Where Capital IQ is blank, the SBIR headcount is filled in. | `205` | 990 real counts (352 Capital IQ + 638 SBIR) |
| Total Revenue [LTM] ($USDmm) | number ($ millions) | Capital IQ value kept as is. Where Capital IQ shows `-` (no figure), the SEC XBRL revenue is filled in, **however old** — an old real figure beats none. Capital IQ's `0` is a reported zero and is left alone. | `564.169` | 6,271 Capital IQ + 9 SEC |
| revenue_source *(new, beside revenue)* | text | `Capital IQ`, or `SEC XBRL (year)` when filled from SEC — the year shows how old it is | `SEC XBRL (2023)` | |

### New fields

| Field | Type | Meaning | How it's derived | Example | Filled |
|---|---|---|---|---|---|
| employees_source | text | Where the employee count came from | `Capital IQ` if Capital IQ had a figure; `SBIR (year)` if filled from the company's most recent SBIR award that reports a headcount; blank if neither | `SBIR (2023)` | 990 |
| sbir_match_name | text | The SBIR company attached to this row, as SBIR spells it | Copied from SBIR. For an ambiguous match, lists the candidates | `AGILIX INC` | 1,089 (994 matched + 95 unconfirmed candidates) |
| sbir_match_by | text | How the match was made (see Matching rules) | `name`, `name+website`, `name+address`, `website`; or `ambiguous` / `unconfirmed` — nothing attached, candidate shown in sbir_match_name | `name` | 994 matched (916 name, 29 name+website, 29 name+address, 20 website); 95 unconfirmed |
| sbir_awarded | Yes / No | Has the company ever had an SBIR or STTR award | `Yes` if matched; `No` if not; blank for the ambiguous row | `Yes` | 994 Yes |
| sbir_active_usdmm | number ($ millions) | Money on awards still running | Sum of `award_amount` over awards with `award_active_today = True`, in $ millions to 3 decimals (like Capital IQ revenue). Full award value — not what is left unspent, and **not free cash** | `0.551` | 74 |
| sbir_active_until | date | When the company's SBIR money runs out | Latest `contract_end_date` among active awards | `2027-07-31` | 74 |
| sbir_agencies | text | Which agencies funded it, and how often | Every award, active or ended, counted per agency, most first | `Department of Energy (4); National Science Foundation (1)` | 994 |
| university_current | text | Research partners on awards still running | Distinct `ri_name` on active awards, `; `-separated | `Stevens Institute of Technology` | 8 |
| university_past | text | Research partners on awards that have ended | Distinct `ri_name` on ended awards, **excluding** anything in `university_current` | `Boston University; MIT` | 234 |

### New fields — Form D

Added after the SBIR block. From `data/cache/formd_clean.csv`.

| Field | Type | Meaning | How it's derived | Example | Filled |
|---|---|---|---|---|---|
| formd_match_name | text | The Form D company attached, as SEC spells it (latest name) | Copied from Form D. For an ambiguous match, lists the candidates | `REVBIO, INC.` | 1,076 (1,000 matched + 76 unconfirmed / ambiguous candidates) |
| formd_match_by | text | How the match was made (see Matching rules) | `name`, `name+address`; or `ambiguous` / `unconfirmed` — nothing attached | `name` | 1,000 matched (986 name, 14 name+address); 75 unconfirmed, 1 ambiguous |
| formd_raised | Yes / No | Has raised private money in the filings we hold (2023 onward) | `Yes` if any real filing (not a merger, not "yet to occur") reports money raised | `Yes` | 950 Yes |
| formd_recent_usdmm | number ($ millions) | Money raised since `combine.formd_recent_from` in `config.yaml` (now 2025-01-01) | Sum of `raised_since_last_filing` over: updates filed on/after the date, and first filings whose **first sale** is on/after it. Mergers and "yet to occur" never count. **Free cash** — unlike SBIR, investors' money can be spent on sponsored research | `3.115` | 422 |
| formd_last_year | year | Last year the company raised new money | Year of the latest filing with `raised_since_last_filing` > 0 | `2025` | |
| formd_industry_group | text | Industry the company gave SEC | Every value across its filings, most recent first | `Biotechnology` | |

Form D has no website, so a similar (not identical) name can only be confirmed by **ZIP code**.
Filings are grouped by **CIK** (SEC's company ID), so a company that renamed
itself keeps all its raises — Liberation Labs Holdings → Liberation
Bioindustries.

### New fields — SEC XBRL

Added after the Form D block. From `data/cache/xbrl_clean.csv`. Public
companies only — figures come from their own SEC filings.

| Field | Type | Meaning | How it's derived | Example | Filled |
|---|---|---|---|---|---|
| xbrl_match_name | text | The company as SEC names it (latest name) | Copied from XBRL | `Amylyx Pharmaceuticals, Inc.` | 313 |
| xbrl_match_by | text | How the match was made | `ticker` — Capital IQ ticker → SEC's ticker list → CIK; `formd_cik` — no usable ticker, but the Form D match's CIK files with SEC (raised privately, then listed; or an OTC / foreign ticker SEC doesn't list) | `ticker` | 289 ticker, 24 formd_cik |
| xbrl_rd_expense_usdmm | number ($ millions) | R&D spend in the latest calendar year reported | Latest `rd_expense` year in `xbrl_clean.csv` | `90.404` | 284 |
| xbrl_rd_year | year | That year | | `2025` | |
| xbrl_cash_usdmm | number ($ millions) | Cash on the latest balance sheet | Latest quarter-end with cash or short-term investments | `23.37` (Corbus) | 293 |
| xbrl_short_term_investments_usdmm | number ($ millions) | Securities it can sell within a year, **same balance sheet** as the cash. Blank = none reported that quarter | Same quarter-end as the cash | `94.571` (Corbus) | |
| xbrl_cash_total_usdmm | number ($ millions) | Everything it could draw on that day | Cash + short-term investments on the same balance sheet (a missing one counts as 0) — or the company's **own reported total** for that day if that is more than 1% larger, which catches investments filed under a label we don't read (and sometimes longer-dated investments it also counts as available) | `117.941` (Corbus, 30 Jun 2026) | 294 |
| xbrl_cash_total_from | text | Where the total came from | `cash + short-term`, or `company's own total` | `cash + short-term` | 294 (274 cash + short-term, 20 company's own total) |
| xbrl_cash_as_of | date | Date of that balance sheet | A quarter-end | `2026-06-30` (277 of 302) | 294 |

SEC revenue has no column of its own: it goes straight into Capital IQ's
revenue column where Capital IQ is blank, and `revenue_source` says so (see
Modified fields). The latest year is used; a real 0 stays 0.

No name matching: a ticker or CIK belongs to exactly one company. Cash and
short-term investments are the closest SEC figures to "money it could spend
now" — like Form D, and unlike SBIR, it is free cash.

### Matching rules

One rule for SBIR and Form D:

1. **Same name → matched** (`name`). Same once case, spaces, punctuation and a
   leading "The" are ignored, and Corp = Corporation, Co = Company. The legal
   form must not contradict: Inc = Inc, Inc = (none given), but Inc ≠ LLC.
   *NeuroFlow, Inc. = NEUROFLOW INC; Genzyme Corp. = GENZYME CORPORATION.*
2. **Similar name → matched only if confirmed.** Similar = the same only once
   Holdings / Group / International / USA are ignored too, or the legal forms
   differ. *Casimir, LLC ~ Casimir, Inc.; Curaleaf, Inc. ~ Curaleaf Holdings, Inc.*
   - both have a website → the websites must match (`name+website`); if they
     differ it is a different company
   - otherwise → the ZIP codes must match (`name+address`)
   - not confirmed → nothing attached, `unconfirmed`, candidate shown
3. **Website only (SBIR)** — no name matches, but the website belongs to
   exactly one SBIR company: a rename (`website`).

A **very short name** ("Meta, Inc.", "PCT, LLC", "SST Corporation") counts as
similar even when identical, and must be confirmed too — Meta, Inc. on Long
Island is not the SBIR "Meta" in San Jose. **Namesakes** (two companies with
the same name) are told apart by website or ZIP; if nothing picks one, the
row is `ambiguous`.

---

## 2. `data/cache/sbir_clean.csv`

One row per SBIR/STTR award. Cleaned from the raw SBIR download; no
matching and no rolling up.

Built by `clean_sbir.py` from `data/sources/sbir/award_data.csv`
(<https://www.sbir.gov/awards>). 16 of the 42 raw columns are used; the
contact and PI names, emails and phone numbers are never read.

| Field | Type | From raw column | Cleaning / meaning |
|---|---|---|---|
| company | text | Company | Trimmed |
| uei | text | UEI | Government's unique company ID. Blank on older awards, so those borrow the UEI of the same company name from its newer awards |
| agency | text | Agency | Funding agency — the cleanest label for what area the award is in |
| award_title | text | Award Title | Trimmed |
| abstract | text | Abstract | Line breaks flattened |
| award_amount | number (USD) | Award Amount | To a number |
| award_year | number | Award Year | As given. The only date on older awards that have no end date |
| contract_end_date | date | Contract End Date | To a date. After this date nothing new can be charged to the award. Blank on about half of all rows, almost all pre-2015 |
| award_active_today | True / False | *(derived)* | `contract_end_date` is on or after the day the file was built. No end date → False |
| ri_name | text | RI Name | Research institution partner. `Stub` placeholder blanked; otherwise exactly as SBIR wrote it. Only STTR awards have one |
| number_employees | number | Number Employees | Headcount as at that award |
| company_website | text | Company Website | As given |
| address | text | Address1 + Address2 | Joined |
| city | text | City | Trimmed |
| state | text | State | Trimmed |
| zip | text | Zip | First 5 digits; anything else blanked |

---

## 3. `data/cache/formd_clean.csv`

One row per Form D **filing** by an operating company. Built by
`clean_formd.py` from `data/sources/formd/` (quarterly downloads). No
matching, no rolling up. Pooled investment funds and co-issuers removed;
the RELATEDPERSONS and SIGNATURES tables (people's names) are never read.

**How Form D works:** a company files a **D** within 15 days of the first
sale in a fundraise, then **D/A** updates while it stays open. Each filing
reports the **total raised so far**, not new money.

| Field | Meaning |
|---|---|
| company, cik, city, state, zip | Who filed. CIK is SEC's company ID and survives renames |
| offering_id | The fundraise this filing belongs to (ID of its first filing we hold) |
| filing_number | 1 = first filing of the fundraise, 2, 3… = updates |
| is_latest_filing | No later update exists |
| filing_date | When this filing reached the SEC |
| first_sale_date | When money first came into this fundraise — same on every filing of it |
| first_sale_yet_to_occur | Announced, nothing sold yet |
| offering_target | What the company is trying to raise. Can rise over time. `Indefinite` = no ceiling |
| total_raised_to_date | Total raised in this fundraise so far, as reported |
| raised_since_last_filing | New money since the previous filing = this total − previous total. First filing: all of it. **Blank** when it can't be known: the previous filing predates 2023, or the previous filing reported an impossible $0 after sales had begun. **0** when a total was corrected downward |
| security_type | equity / debt / SAFE / option-warrant / other |
| number_of_investors | People who have invested so far |
| industry_group | Industry the company chose, e.g. Biotechnology |
| revenue_range | Self-reported; ~90% "Decline to Disclose" |
| company_founded_year | Given only if under 5 years old — blank usually means older |
| is_business_combination | Filing relates to a merger or acquisition, not a normal raise |
| accession_number | This filing's own SEC ID |

---

## 4. `data/cache/xbrl_clean.csv`

One row per public company per concept per period. Built by
`clean_xbrl.py` from the frames that `fetch_sec_xbrl.py` downloads into
`data/sources/xbrl/`. No matching.

| Field | Meaning |
|---|---|
| company, cik, ticker | Who reported it. Ticker is the common-stock one (GOOG, not GOOGN) |
| concept | `rd_expense`, `revenue` (a whole year) or `cash`, `short_term_investments`, `cash_and_short_term_investments` (one day; the last is the company's own total, used only as a check) |
| period | `CY2025` = calendar year 2025; `CY2026Q2I` = as at 30 June 2026 (I = instant) |
| period_type | `year` or `instant` |
| period_start, period_end | Dates the figure covers; instant figures have only an end |
| value_usd | The figure, in dollars |
| tag | Which SEC tag it came from. Each concept has several alternatives; the first with a non-zero figure is used, never a sum |
| accession | The filing it came from |
| location | State / country SEC holds for the company |

---

## Things to remember

- **"Active" depends on the day the file is built.** Re-run
  `python clean_sbir.py` and `python combine_companies.py` whenever you
  download a new `award_data.csv`.
- **SBIR employee counts can be old** — the year in `employees_source` shows
  how old.
- **Award titles and abstracts** are only in `sbir_clean.csv` for now; how
  they feed matching is still to be decided.
- **SBIR money is not free cash.** It is tied to the funded project, so no
  SBIR dollar figure says whether a company can sponsor research now — which
  is why `sbir_total_usd` was dropped. Form D money *is* free cash.
- **Rebuild order after new downloads:** `python clean_sbir.py`,
  `python clean_formd.py`, `python clean_xbrl.py`, then
  `python combine_companies.py`.
- **XBRL cash needs a fetch from your Mac's Terminal** (SEC can't be reached
  from Claude's environments): `python fetch_sec_xbrl.py`. Frames for a
  recent quarter keep filling for ~120 days; the fetch re-downloads those
  automatically on the next run.
