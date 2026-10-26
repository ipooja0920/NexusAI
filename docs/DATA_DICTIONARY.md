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
| Total Employees [Latest Annual] | number | Capital IQ value kept as is. Where Capital IQ is blank, the SBIR headcount is filled in. | `205` | 972 real counts (352 Capital IQ + 620 SBIR) |
| Total Revenue [LTM] ($USDmm) | number ($ millions) | Capital IQ value kept as is. Where Capital IQ shows `-` (no figure), the SEC XBRL revenue is filled in, **however old** — an old real figure beats none. Capital IQ's `0` is a reported zero and is left alone. | `564.169` | 6,271 Capital IQ + 9 SEC |
| revenue_source *(new, beside revenue)* | text | `Capital IQ`, or `SEC XBRL (year)` when filled from SEC — the year shows how old it is | `SEC XBRL (2023)` | |

### New fields

| Field | Type | Meaning | How it's derived | Example | Filled |
|---|---|---|---|---|---|
| employees_source | text | Where the employee count came from | `Capital IQ` if Capital IQ had a figure; `SBIR (year)` if filled from the company's most recent SBIR award that reports a headcount; blank if neither | `SBIR (2023)` | 972 |
| sbir_match_name | text | The SBIR company attached to this row, as SBIR spells it | Copied from SBIR. For an ambiguous match, lists the candidates | `AGILIX INC` | 1,089 (957 matched + 132 unconfirmed candidates) |
| sbir_match_by | text | How the match was made (see Matching rules) | `name`, `name+website`, `name+address`, `website`; or `ambiguous` / `unconfirmed` — nothing attached, candidate shown in sbir_match_name | `name` | 958 matched (845 name, 40 name+website, 53 name+address, 20 website); 131 unconfirmed |
| sbir_awarded | Yes / No | Has the company ever had an SBIR or STTR award | `Yes` if matched; `No` if not; blank for the ambiguous row | `Yes` | 958 Yes |
| sbir_active_usdmm | number ($ millions) | Money on awards still running | Sum of `award_amount` over awards with `award_active_today = True`, in $ millions to 3 decimals (like Capital IQ revenue). Full award value — not what is left unspent, and **not free cash** | `0.551` | 73 |
| sbir_active_until | date | When the company's SBIR money runs out | Latest `contract_end_date` among active awards | `2027-07-31` | 73 |
| sbir_agencies | text | Which agencies funded it, and how often | Every award, active or ended, counted per agency, most first | `Department of Energy (4); National Science Foundation (1)` | 957 |
| university_current | text | Research partners on awards still running | Distinct `ri_name` on active awards, `; `-separated | `Stevens Institute of Technology` | 8 |
| university_past | text | Research partners on awards that have ended | Distinct `ri_name` on ended awards, **excluding** anything in `university_current` | `Boston University; MIT` | 229 |
| sbir_award_titles | text | Titles of all its SBIR/STTR awards | Newest award year first; placeholders like "Not Available" left out; separated by ` \| `; a title repeated (Phase I then II) shown once | `Novel Antigen-Specific Immunomodulatory Tregitope-based Therapy … \| …` | 958 |
| sbir_abstracts | text | Abstracts of all its awards — what each project is about | Newest award year first; "Redacted" / "Not Available" / "TBD" left out; separated by ` \|\| `; repeats shown once. If more than one Excel cell holds (~32,000 characters), the oldest are left out and counted as `(+N more)` | | 933 (56 cut short) |

### New fields — Form D

Added after the SBIR block. From `data/cache/formd_clean.csv`.

| Field | Type | Meaning | How it's derived | Example | Filled |
|---|---|---|---|---|---|
| formd_match_name | text | The Form D company attached, as SEC spells it (latest name) | Copied from Form D. For an ambiguous match, lists the candidates | `REVBIO, INC.` | 1,076 (995 matched + 81 unconfirmed / ambiguous candidates) |
| formd_match_by | text | How the match was made (see Matching rules) | `name`, `name+address`; or `ambiguous` / `unconfirmed` — nothing attached | `name` | 995 matched (980 name, 15 name+address); 80 unconfirmed, 1 ambiguous |
| formd_raised | Yes / No | Has raised private money in the filings we hold (2023 onward) | `Yes` if any real filing (not a merger, not "yet to occur") reports money raised | `Yes` | 946 Yes |
| formd_recent_usdmm | number ($ millions) | Money raised since `combine.formd_recent_from` in `config.yaml` (now 2025-01-01) | Sum of `raised_since_last_filing` over: updates filed on/after the date, and first filings whose **first sale** is on/after it. Mergers and "yet to occur" never count. **Free cash** — unlike SBIR, investors' money can be spent on sponsored research | `3.115` | 422 |
| formd_last_year | year | Last year the company raised new money | Year of the latest filing with `raised_since_last_filing` > 0 | `2025` | |
| formd_industry_group | text | Industry the company gave SEC | Every value across its filings, most recent first | `Biotechnology` | |

Form D has no website, so a similar (not identical) name can only be confirmed by **ZIP code**.
Filings are grouped by **CIK** (SEC's company ID), so a company that renamed
itself keeps all its raises — Liberation Labs Holdings → Liberation
Bioindustries. A CIK is found under **every name it has filed under**, so a
company Capital IQ still lists by its old name is matched too (Durin
Technologies → Durin Life Sciences; `formd_match_name` shows the newest
name).

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

### New fields — ClinicalTrials.gov

Added after the XBRL block. From `data/cache/ctgov_clean.csv`. **Withdrawn**
trials (never ran) are left out of every column; trials with status
**UNKNOWN** count as ended.

| Field | Type | Meaning | How it's derived | Example | Filled |
|---|---|---|---|---|---|
| ct_match_name | text | The company as ClinicalTrials.gov spells it (commonest spelling) | For unconfirmed / ambiguous rows, the candidate(s) | `Abiomed Inc.` | 1,645 |
| ct_match_by | text | How the match was made | `name` only — trial records carry no sponsor website or address, so a similar name cannot be confirmed and stays `unconfirmed` | `name` | 909 name; 736 unconfirmed |
| ct_university_current | text | Universities / hospitals / non-profits on its **running** trials (names with a company legal form and no academic word - CROs, drug makers ClinicalTrials.gov files as non-industry - are left out) | Partners of running trials, most trials first, top 10 then `(+N more)` | `Yale University; UConn Health` | 148 |
| ct_university_past | text | The same for **ended** trials | Partners of ended trials, minus any in current | `Duke University` | 337 |
| ct_trials_active | number | Running trials | Count; 0 = matched but nothing running | `3` | 909 (443 above 0) |
| ct_active_phase | text | Highest phase among running trials | `Phase 1` … `Phase 4`; blank if none running or no phases (e.g. device studies) | `Phase 3` | 319 |
| ct_last_start_year | year | Year its most recent trial started (or is planned to) | Latest start year | `2025` | 907 |
| ct_conditions | text | Diseases studied | Most trials first, top 10 then `(+N more)` | `Obesity; Type 2 Diabetes` | 909 |
| ct_treatment_types | text | Kinds of treatment tested | Drug / Device / Biological / Procedure / …, most trials first ("Other" left out) | `Device; Drug` | 860 |

### New fields — OpenAlex

Added after the ClinicalTrials.gov block. From `data/cache/openalex_companies.csv`.
OpenAlex names carry a country tag ("Pfizer (United States)"), which matching
ignores; they rarely carry Inc / LLC, so most matches are confirmed by website.
A company OpenAlex keeps twice (as a funder and as a publisher, unlinked) is
treated as one.

| Field | Type | Meaning | How it's derived | Example | Filled |
|---|---|---|---|---|---|
| oa_match_name | text | The company as OpenAlex names it | For unconfirmed rows, the candidate | `Abiomed (United States)` | 1,013 |
| oa_match_by | text | How the match was made | `name`, `name+website`, `website`; or `unconfirmed` (similar name, website differs or missing). `website` (no name match) only when no other Capital IQ company has the same website — a parent's website shared by its subsidiaries identifies none of them | `name+website` | 757 matched (91 name, 573 name+website, 93 website); 256 unconfirmed |
| oa_topics | text | Its 5 most frequent research topics | From OpenAlex | `Computational Drug Discovery Methods; …` | 579 |
| oa_last_funded_year | year | Latest year a paper it funded was published | Latest year among papers it funded since 2005 (any author). A few companies funded papers but none by a university, so this can be filled while `oa_funded_university` is False | `2025` | 134 (89 in 2025 or later) |
| oa_funded_university | True / False | A university or hospital (any country) wrote papers it funded since 2005 | | `True` | 131 True |
| oa_coauthored_university | True / False | Its own staff wrote papers with a university or hospital since 2005 | | `True` | 645 True |

### Matching rules

One rule for SBIR and Form D:

1. **Same name → matched** (`name`). Same once case, spaces, punctuation and a
   leading "The" are ignored, and Corp = Corporation, Co = Company. The legal
   form must be the same: Inc = Inc, but Inc ≠ LLC and Inc ≠ (none given).
   *NeuroFlow, Inc. = NEUROFLOW INC; Genzyme Corp. = GENZYME CORPORATION.*
   Bracketed text and a country added to the name are ignored too (not as the
   first word): *AlaMab Therapeutics (Shanghai) Inc. = AlaMab Therapeutics Inc.;
   Zydus Pharmaceuticals USA, Inc. = Zydus Pharmaceuticals (USA) Inc.*
2. **Similar name → matched only if confirmed.** Similar = the same only once
   Holdings / Group / International / USA are ignored too, or the legal forms
   differ or one is missing. *Casimir, LLC ~ Casimir, Inc.; Abceutics Inc. ~
   Abceutics; Curaleaf, Inc. ~ Curaleaf Holdings, Inc.*
   - both have a website → the websites must match (`name+website`); if they
     differ it is a different company
   - otherwise → the ZIP codes must match (`name+address`)
   - not confirmed → nothing attached, `unconfirmed`, candidate shown
3. **Website only (SBIR)** — no name matches, but the website belongs to
   exactly one SBIR company: a rename (`website`).

A source company is looked up under every name it has used and with country
words removed, on both sides (*Anchiano Therapeutics Ltd. = Anchiano
Therapeutics Israel Ltd.*). SBIR awards with no UEI (older ones) are grouped
into companies by the same name rule, legal form included — *PHT Corporation
(Boston) and PHT, Inc. (Pittsburgh) are two companies*.

A name of **3 characters or fewer** — an acronym ("PCT, LLC", "SST
Corporation", "HSS, Inc.") — counts as similar even when identical, and must
be confirmed too. Any longer identical name is the same company ("Abyrx,
Inc.", "Eisai Inc.", "Noom, Inc."). **Namesakes** (two companies with
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

## 5. `data/cache/ctgov_clean.csv`

One row per **trial per company** on it, from ClinicalTrials.gov. Built by
`clean_clinicaltrials.py` from the pages `fetch_clinicaltrials.py` downloads
(trials where a company is the lead sponsor **or** a collaborator). No
matching. No person is named: investigators and contacts are never downloaded.

| Field | Meaning |
|---|---|
| company | The company, as ClinicalTrials.gov spells it |
| role | `lead sponsor` or `collaborator` |
| nct_id, title | The trial's ID (NCT…) and short title |
| status | ClinicalTrials.gov status, e.g. RECRUITING, COMPLETED |
| is_active | True = running now (recruiting, not yet recruiting, active, enrolling by invitation). False = completed, terminated, suspended, withdrawn, or status unknown |
| study_type | INTERVENTIONAL (tests a treatment) or OBSERVATIONAL |
| phase, phase_number | e.g. `Phase 1/2` and its highest number (2). Blank for studies without phases |
| start_date, start_year | When the trial started (or is planned to) |
| completion_date | Planned or actual end |
| last_update | When the record was last updated |
| enrollment | Number of participants |
| conditions | Diseases studied, e.g. `Type 2 Diabetes` |
| interventions | Treatments tested, e.g. drug or device names |
| intervention_types | Kind of treatment: DRUG, DEVICE, BIOLOGICAL, PROCEDURE, BEHAVIORAL, … |
| partners | Universities, hospitals and foundations on the trial, as written. Only names containing an institution word (University, Hospital, Health, Institute, ...) are kept, so a sponsor that is an individual doctor never appears; about 10% of partner mentions, mostly foreign organisations, are lost with them |
| other_companies | Other companies on the same trial |
| sites_total, sites_us, sites_ct | Number of trial sites: all, in the US, in Connecticut |
| ct_sites | Connecticut sites, e.g. `UConn Health (Farmington)` |

Expanded-access programmes (not trials) are dropped.

---

## 6. `data/cache/openalex_companies.csv` and `openalex_ties.csv`

From OpenAlex, built by `clean_openalex.py` from what `fetch_openalex_ties.py`
downloaded (counts only — no paper titles, no author names). Covers the 1,282
US companies in OpenAlex whose name or website could be one of ours; the real
match happens at the combine step.

**openalex_companies.csv** — one row per OpenAlex company

| Field | Meaning |
|---|---|
| institution_id, funder_id | OpenAlex IDs: I… = the company as a research publisher, F… = as a funder |
| company, website, city | As OpenAlex has them |
| topics | Its 5 most frequent research topics |
| papers_since_2005, papers_by_year | Papers its own staff wrote, total and per year (`2025:40; 2024:35`) |
| funded_papers_since_2005, funded_papers_by_year | Papers it funded, total and per year |
| funded_universities | Universities / hospitals that wrote papers it funded, most papers first: `Harvard University (444); University of California, San Francisco (417); …` — all countries; if more than one Excel cell holds (big pharma), the smallest are counted as `(+N more)` — the full list is in openalex_ties.csv |
| coauthor_universities | Universities / hospitals its staff wrote papers with, same format |

**openalex_ties.csv** — one row per company per university / hospital

| Field | Meaning |
|---|---|
| role | `funded` — the university wrote papers the company paid for; `coauthored` — the company's staff wrote papers with it |
| university, university_type, university_country | OpenAlex institutions of type education or healthcare only, any country |
| papers_2005_2023, papers_2024_on, papers_total | Papers in each period |

---

## 7. `data/companies_enriched.xlsx` — scores

Built by `enrich_companies.py` from the combined file. Only the combined
columns a score is computed from are carried across (`employees_source`,
`revenue_source`, the funding inputs `formd_recent_usdmm`, `formd_raised`,
`xbrl_cash_total_usdmm`, `xbrl_cash_as_of`, `xbrl_rd_expense_usdmm`,
`xbrl_rd_year`, `sbir_active_usdmm`, `oa_funded_university`,
`oa_last_funded_year`, and the academic inputs `oa_coauthored_university`,
`sbir_awarded`, `university_past`, `university_current`,
`ct_university_past`, `ct_university_current`; `ct_trials_active` only when
its penalty is switched on). Topics, trial conditions and SBIR titles appear
through `research_focus`; everything else stays in the combined file.
Settings in `config.yaml`.

| Field | Meaning |
|---|---|
| distance_score | exp(−miles / 400) from UConn Storrs — unchanged |
| employee_score | employees / 1,000, capped at 1 — unchanged |
| revenue_score | Size fit: **1.0 for $100m–$1bn**; tapers to 0.2 at $5bn and stays 0.2; $5–100m = 0.4; under $5m = 0.2; no revenue = 0.5 |
| funding_score | Money available now, best of: Form D raised since 2025 ($0.5m → 0, $20m → 1), SEC cash + short-term investments ($5m → 0, $100m → 1), SEC R&D ($1m → 0, $50m → 1), running SBIR/STTR awards ($0.1m → 0, $2m → 1), funded university papers in 2025+ (→ 1). Log scales; older figures pulled towards 0.5. No record = 0.5; Form D "raised: Yes" with nothing recent and revenue missing or < $100m = 0.55 |
| funding_reason | The evidence behind it. For a company with $1bn+ revenue, an SEC R&D figure under 0.5% of revenue is ignored and the reason says so (J&J's SEC R&D tag held $109m against ~$98bn revenue) — `funding.rd_sanity` in config |
| academic_adjustment | Added to the partnership score at match time: +0.10 funded university papers, +0.05 each past SBIR university partner / past trial partner / co-wrote with universities, +0.03 any SBIR award (bonus capped at +0.15); −0.05 each current SBIR or trial university partner (capped at −0.10). Running trials: no penalty (switchable in config) |
| academic_reason | Which of those applied |
| research_focus | OpenAlex topics, trial conditions and the 3 newest SBIR project titles (kept for reference) |
| company_profile | **What the matching reads about the company.** For the ~2,000 companies with SBIR, ClinicalTrials.gov or OpenAlex text: an AI-written ~150-word profile from the Capital IQ description + SBIR titles and abstracts (newest first, first 6,000 characters) + trial conditions and treatment types + OpenAlex topics — facts from those sources only, no people's names, no dollar amounts. Everyone else: the Capital IQ description. Embedded for the pre-filter and read by the alignment, partnership and justification prompts. Cached in `data/cache/company_profiles.json` |
| company_status | Capital IQ "Company Status": Operating, Operating Subsidiary, Acquired, Out of Business, Liquidating, Reorganizing |
| parent_company | The owner, when the description says "operates as a subsidiary of X" (e.g. Biorez → CONMED Corporation) |
| organisation_type | `Company`, `Contract manufacturer`, `Hospital / health system` or `Clinic / care provider` — from the name and the opening of the Capital IQ description. Contract manufacturer: the description says contract manufacturing / CDMO / outsourced manufacturing / manufacturing services (unless it first says the company develops its own products). A description saying it develops / manufactures / designs / sells makes it a Company whatever the name. Veterinary care settings (animal hospitals, vet clinics and practices, DVMs) count as clinics; animal-health companies (drugs, feed, diagnostics) are Companies. `match_faculty.py` recomputes this and parent_company from the description at every run, so a rule fix needs no re-enrich |
| profile_from | `AI: Capital IQ description + sources`, `Capital IQ description`, or `Capital IQ description + research focus` (an AI profile could not be written) |

Final score (`match_faculty.py`, weights unchanged): alignment 30%, distance
25%, partnership 15% (AI score + academic_adjustment), revenue 15%, funding
10%, employees 5%.

---

## 8. Faculty match workbook — `data/output/<Name> Match <date>.xlsx`

Faculty come from the **BD-IFR Faculty Registration** export
(`data/input/BDIFR Faculty Registration From-documents.csv`, run one with
`--row N` — header = row 1 — or `--faculty "Name"`), or the old Faculty
Database (Flag = N). From the form the matching uses: keywords, research
description, nature of research, primary and secondary industry sector, and
the "anything else" note (industry interests). Emails, IDs, years in post and
workflow fields are never read. The profile used is in **Run Info**.

Companies are ranked **separately in three size tiers** (revenue, `size_tiers`
in config.yaml); hospitals and clinics are taken out of those tabs and ranked on
one sheet of their own. Workbook tabs:

| Tab | What it lists |
|---|---|
| Small - top 50 (pre-filter) | companies under $100m revenue or revenue unknown. AI-scored: the 300 closest to the full research profile + up to 30 more from each keyword / interest search (~450). `--full` scores every small company instead (hours) and the tab is then "(all companies)" |
| Mid ($100m-$1bn) - top 50 | companies $100m – $1bn, all AI-scored |
| Large ($1bn+) - top 50 | companies $1bn and above, all AI-scored |
| Hospitals & Clinics - top 50 | hospitals, health systems, clinics and practices of every size (Size column), from the same scoring |
| All Companies | every company, ranked within its size tier |
| Run Info | the research profile used, models, counts, weights |

In each tier: companies and care providers each send their top 100 (by
preliminary score: distance, size, alignment) to the partnership step; the top
50 companies - verdict first, then overall score - fill the tier tab, and the providers' best go to
the Hospitals & Clinics sheet (top 50 across all sizes). A minimum alignment
score can be set (`funnel.min_alignment`, off by default).

| Column | Meaning |
|---|---|
| Organisation Type | Company / Contract manufacturer / Hospital / health system / Clinic / care provider — all listed, labelled, not removed |
| Status, Parent Company | Capital IQ status; the owner of a subsidiary — the partner to approach |
| Justification | 2–3 sentences: which of the researcher's keywords / problems / methods the company's work touches, then distance, size, money and university record. Written ONLY from our data (see "No outside knowledge" below) |
| Size | Small / Mid / Large (above) |
| Overall Score | Final weighted score (above) |
| Sector Match | `Primary sector` / `Secondary sector`: the company's Capital IQ industry group is the one the researcher named. Capital IQ here holds only two industry groups (Health Care Equipment and Services; Pharmaceuticals, Biotechnology and Life Sciences) |
| Alignment Score (1-9), Reason | AI: research ↔ company profile (the middle of 3 answers) |
| Alignment Votes | The 3 answers the middle score came from |
| Partnership Score (1-9), Reason | AI: how likely to partner, plus the academic track record |
| Funding Score (0-1), Reason | From the enriched sheet |
| Company Profile | The text the AI read |

**Rules in every tab** (`funnel` in config.yaml):

- Companies Capital IQ marks **Acquired, Out of Business or Liquidating** are left
  out of the tabs (1,507); All Companies shows them as "Left out (…)".
- **Minimum alignment per size tier** (`funnel.min_alignment_by_tier`):
  Mid and Large need an AI alignment score of at least **4/9** to reach the
  partnership step and a tab (they have few companies, so unrelated ones -
  gene editing, oncology - otherwise filled them); Small has no minimum.
- **Alignment rewards methods over condition.** The AI judges first by HOW the
  researcher works (what they measure, build, test or use - their keywords)
  and only then by the condition or body part. A company treating the same
  condition with a different kind of product (implant, drug, biologic) scores
  5-6, not 8-9; an unrelated company scores 1-2 (NA only for no description).
- **Alignment is asked 3 times per company** (`openai.alignment_votes`) and
  the middle score is used - one ask can swing by several points (8 one run,
  4 the next). `Alignment Votes` shows all three answers (e.g. "8, 4, 7");
  the reason shown is the one given with the middle score. A company is NA only when all three answers are NA; otherwise NA answers are ignored. Each answer is
  cached on its own, so re-runs cost nothing extra.
- **Alignment 1-2 means no named link.** A company whose description names
  none of the researcher's methods, tools, conditions, body parts or patient
  groups scores 1-2, not 3; 3-4 needs something specific but loose.
- **Capital IQ stub descriptions** - blank, "-", or code-like abbreviations
  ("Ofcs Clns Of Mdl D", "Sklld Nrsng Cr Fclt") - count as no description
  (about 380 companies): not scored, and no pre-filter place.
- **A corporate group is listed once.** A parent and its subsidiaries (from
  "operates as a subsidiary of X", the same named parent, or the same name
  without ticker / legal form / Holdings - for names of two or more words)
  appear once per tab, by their best-placed member; across the size tabs the
  member with the higher alignment stays (a tie goes to the larger tier). The
  others show "Same group as X (listed)" in All Companies, and the tab fills
  the place with its next company.
- **Tabs are ordered by verdict first**: every Strong fit (alignment 7-9),
  then Moderate (5-6), then Weak (1-4), each group by overall score. The top
  50 are taken in that order too, so a weak research match never outranks a
  strong one just because it is near Storrs or large. (Overall Score is
  unchanged and still shown.)
- **Justifications start with the verdict**: "Strong fit" (alignment 7-9),
  "Moderate fit" (5-6), "Weak fit" (1-4) or "Fit unclear" (NA).
- A partnership answer of **NA** ("not known") counts as **neutral (0.5)** after
  the 1–9 scores are scaled — not as the lowest score. The partnership prompt
  is given the company's university record (SBIR and trial university
  partners, papers it funded or co-wrote) so it judges from evidence.
- The **Small pre-filter** takes the 300 small companies closest to the full
  profile (as always) and, for registration-form faculty, ADDS up to 30 more
  from each keyword and from the industry-interests note searched on their own
  (`prefilter.extra_per_search`) - about 450 in all, only companies with a
  description (one without can never be scored). `Found By` in All
  Companies says which search brought a company in.

**Faculty publications** (`data/input/publications/*.csv`, the scholarly
contributions export; matched to the faculty by `Name`; only Name, Title,
Type and Year are read - never NetID or Description). Each title gets a
weight = age x type: last 5 years (or no year - forthcoming) 1.0, 6-10 years
0.6, 11-15 years 0.4, older 0.25; articles, letters, reviews 1.0, posters /
presentations 0.5, errata left out; a title listed twice counts once
(`publications` in config.yaml). They are used twice:
- **pre-filter**: each title is searched on its own; a company's score is its
  best weighted match with any title, and this search adds up to 30 small
  companies (`Found By` = "publications"). The 300 from the registration
  profile are unchanged - the titles are not mixed into that search.
- **AI profile**: the top 12 titles by weight are added after the
  registration answers, labelled as supporting evidence (the keywords and
  description come first). Justifications may name one; the grounding check
  accepts only titles that are in that list.
Run Info shows how many titles were used and how many companies the search
added. A faculty member with no publications file is matched as before.

**No outside knowledge.** Every AI step is told to use only the text it is
given and nothing it may "know" about a company:
- company profile: only the Capital IQ description and SBIR / trial / OpenAlex text
- alignment: only the company's name, industry and profile
- partnership: only the profile and the university track record from our data
- justification: only the researcher profile and a list of facts from our data
  (profile, organisation type, status / parent, sector, scores, university
  record, money, distance, size). Each justification is then **checked
  automatically**: any figure or university name not in those facts sends it
  back once; if it still adds one, a plain justification built from the data
  is used instead.

**All Companies** lists every company, grouped Small / Mid / Large. `Rank` is
the position within the tier: the listed ones by overall score, then the rest
of the partnership-scored ones by overall score, then every other company with
an alignment score by `Preliminary Score` (distance, size and alignment).
Companies without a score are not ranked. Each row shows its stage within the
tier, `In Pre-filter` (Yes/No for small companies, "All
scored" for mid and large) and scores. Alignment scores are cached
(`data/cache/alignment_scores.json`), so an interrupted run resumes and the
second pass reuses the first pass's 300 scores. Partnership scores are cached
per company and profile text, shared by every faculty.

---

## Things to remember

- **One row per company - only when known.** Capital IQ lists some companies
  more than once. Two rows are merged only when they have the same name (by
  the matching rule) AND the same website - or, with no website to compare,
  the same ZIP code. The row with the most Capital IQ fields filled in is
  kept. 56 rows are left out (21,750 rows against 21,806 in the export);
  same-name rows that cannot be confirmed stay as separate rows. No column
  is added; `data/review/combined_qa_review.xlsx` lists every same-name row
  as kept, left out, or kept separately.

- **"Active" SBIR awards are judged on the day `combine_companies.py` runs**
  (contract end date on or after that day), not on the day `sbir_clean.csv`
  was built.
- **Research partners** (`university_current` / `university_past`) are listed
  once whatever their capitals, and never the company itself (a few awards
  name the company as its own partner).
- **A Capital IQ employee count of 0** is treated as blank and filled from
  SBIR (3 rows).
- **Capital IQ's employee and revenue columns** are written as numbers ("-"
  stays as "-"), so Excel can sort and sum them.

- **"Active" depends on the day the file is built.** Re-run
  `python clean_sbir.py` and `python combine_companies.py` whenever you
  download a new `award_data.csv`.
- **SBIR employee counts can be old** — the year in `employees_source` shows
  how old.
- **Award titles and abstracts** are in the combined file (`sbir_award_titles`,
  `sbir_abstracts`); abstracts are the applicants' own text and occasionally
  mention researchers by name.
- **SBIR money is not free cash.** It is tied to the funded project, so no
  SBIR dollar figure says whether a company can sponsor research now — which
  is why `sbir_total_usd` was dropped. Form D money *is* free cash.
- **Rebuild order after new downloads:** `python clean_sbir.py`,
  `python clean_formd.py`, `python clean_xbrl.py`,
  `python clean_clinicaltrials.py`, then `python combine_companies.py`.
- **XBRL cash needs a fetch from your Mac's Terminal** (SEC can't be reached
  from Claude's environments): `python fetch_sec_xbrl.py`. Frames for a
  recent quarter keep filling for ~120 days; the fetch re-downloads those
  automatically on the next run.
