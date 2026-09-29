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

### Modified field

| Field | Type | What changed | Example | Filled |
|---|---|---|---|---|
| Total Employees [Latest Annual] | number | Capital IQ value kept as is. Where Capital IQ is blank, the SBIR headcount is filled in. | `205` | 1,010 real counts (352 Capital IQ + 658 SBIR) |

### New fields

| Field | Type | Meaning | How it's derived | Example | Filled |
|---|---|---|---|---|---|
| employees_source | text | Where the employee count came from | `Capital IQ` if Capital IQ had a figure; `SBIR (year)` if filled from the company's most recent SBIR award that reports a headcount; blank if neither | `SBIR (2023)` | 1,010 |
| sbir_match_name | text | The SBIR company attached to this row, as SBIR spells it | Copied from SBIR. For an ambiguous match, lists the candidates | `AGILIX INC` | 1,066 |
| sbir_match_by | text | How the match was made | `name` — normalised names are equal; `website` — no name match but the website domain belongs to exactly one SBIR company; `name+suffix` — names match and websites differ, but the legal suffix (Inc / LLC / Ltd…) agrees; `ambiguous` — one name, several SBIR companies, nothing attached | `name` | 1,066 |
| sbir_awarded | Yes / No | Has the company ever had an SBIR or STTR award | `Yes` if matched; `No` if not; blank for the ambiguous row | `Yes` | 21,805 (1,065 Yes) |
| sbir_active_usd | number (USD) | Money on awards still running | Sum of `award_amount` over awards with `award_active_today = True`. Full award value — not what is left unspent | `551373` | 76 |
| sbir_active_until | date | When the company's SBIR money runs out | Latest `contract_end_date` among active awards | `2027-07-31` | 76 |
| sbir_agencies | text | Which agencies funded it, and how often | Every award, active or ended, counted per agency, most first | `Department of Energy (4); National Science Foundation (1)` | 1,062 |
| university_current | text | Research partners on awards still running | Distinct `ri_name` on active awards, `; `-separated | `Stevens Institute of Technology` | 9 |
| university_past | text | Research partners on awards that have ended | Distinct `ri_name` on ended awards, **excluding** anything in `university_current` | `Boston University; MIT` | 245 |

### Matching rules

- **Name first, website second.** Names are compared after normalising
  (case, punctuation and suffixes like Inc / LLC / Corp removed).
- **Same name, several SBIR companies** — the website, then the state,
  picks one. If neither does, the row is `ambiguous` and nothing is attached.
- **A match is rejected** only if the websites actively disagree, or the
  name is too generic on its own. A different state never rejects a match.
- **Websites disagree but the legal suffix agrees** (NeuroFlow, Inc. =
  NEUROFLOW INC) — accepted as `name+suffix`. This catches subsidiaries that
  Capital IQ lists under their parent's website. It never overrides the
  too-generic rule, so short acronyms like `ACT` or `DPD` stay rejected.

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

## Things to remember

- **"Active" depends on the day the file is built.** Re-run
  `python clean_sbir.py` and `python combine_companies.py` whenever you
  download a new `award_data.csv`.
- **SBIR employee counts can be old** — the year in `employees_source` shows
  how old.
- **Award titles and abstracts** are only in `sbir_clean.csv` for now; how
  they feed matching is still to be decided.
