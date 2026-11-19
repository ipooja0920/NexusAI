# BridgeAI — Product Specification

**Faculty ↔ Industry Matching Pipeline**
*University of Connecticut · v2 architecture, updated November 2026*

> BridgeAI was developed under the working name NexusAI; the project folder,
> GitHub repository and the `nexus/` package keep that name. How to run it
> is in [README.md](README.md); every output column is defined in
> [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md).

---

## 1. Purpose

BridgeAI identifies companies that are strong candidates for research
partnerships with UConn faculty. Given a faculty member's research profile
and a Capital IQ company list enriched from public datasets, it produces one
workbook per faculty member with ranked, explained partner lists — top 50
**Small**, **Mid** and **Large** companies and top 50 **Hospitals &
Clinics** — scored on research alignment, partnership likelihood, money
available, proximity and company size.

## 2. Background: the existing (v1) architecture

The first-generation system was built around a **per-professor screening
drop** workflow:

1. A staff member manually ran a Capital IQ screen for one professor and
   saved the result (companies + the professor's criteria paragraph in
   cell C1) into a SharePoint **DropFolder** synced via OneDrive.
2. A **watcher script** (`WatcherProgram.py`) running on a dedicated
   machine detected the file, emailed status notifications, and launched
   the processing program (`ProgramTesting9.py`) as a subprocess.
3. `ProgramTesting9.py` geocoded every address, scored every company
   against the criteria paragraph via OpenAI (1–9 rubric), selected a
   top 40 for secondary partnership/funding scoring, computed a
   6-component weighted score, and wrote results into a copy of a
   formatting template (`Final_Company_Score_Generator_Copy.xlsx`),
   plus an optional PowerPoint for the top 10.

### v1 pain points

| Issue | Consequence |
|---|---|
| One screening file per professor, prepared manually | Capital IQ work repeated for every request |
| Everything recomputed per run (geocoding, parsing) | ~1 sec/address geocoding on every single run |
| Only the company's *name* was sent to the AI | The AI could judge a company only if it already "knew" it from training. Small/private companies it had never heard of got "NA" (no score) and sank in the rankings — even when they were actually good fits. See §3.4 |
| Hardcoded OS paths (separate Mac/Windows script versions) | Code forked into multiple variants |
| Credentials hardcoded in watcher scripts | Security exposure |
| No record of which professors had been processed | Manual tracking |

## 3. The v2 architecture

v2 inverts the data flow: instead of one screening file per professor,
there is **one shared company list** per run (a Capital IQ extract, enriched
once and reused across faculty) and a **faculty source** — the BD-IFR
registration form export, or the older Faculty Database with a `Flag`
column.

### Guiding principle

> **Never pay twice for work that doesn't change between runs.**

Everything that depends only on the *company* is computed once and cached.
Only work that depends on the *specific professor* runs per matching — and
its AI answers are cached too, so a re-run or an interrupted run is cheap.

### 3.1 Inputs

| Input | Used for |
|---|---|
| Capital IQ export (`companies_raw.xlsx`) | The company list. Columns are found by **header name** (any order); `check_export.py` reports a missing or nearly empty column before anything runs. |
| Registration form (`BDIFR Faculty Registration From-documents.csv`) | The research profile: keywords, research description, nature of research, primary/secondary industry sector, department, industry-interest notes. Contact and identity fields are never read. |
| Scholarly-contributions CSVs (`data/input/publications/`) | Up to 12 titles, weighted by age and type, are added to the profile; all titles are also searched as their own shortlist query. Only Name, Title, Type and Year are used. |
| Faculty notes (`data/input/faculty_notes.yaml`, optional) | Per-faculty adjustments: extra research text, named leads, a wider "nearby" distance, publications to ignore (§3.8). |

**Name matching.** A faculty member is found by full name ignoring accents,
case and punctuation, then by first + last name (titles such as Dr, Prof and
a trailing MD ignored; a leading "Md" is part of the name). The same rule
links a faculty member to their publications. Two people sharing a first and
last name must be run with `--row`: a first + last match that fits two
people stops the run, and an identical full name listed twice takes the first.

### 3.2 Stage 0 — Company data (`combine_companies.py`, `enrich_companies.py`)

Run once per company list (by `./run_enrich.sh`), and again only when the
export or a source changes.

**Combine** attaches facts from public datasets to each Capital IQ company
(§5) and computes no scores, so the matching of sources can be checked on
its own. A company listed twice is kept once only when it is known to be the
same company (same name and same website, or same ZIP when there is no
website).

**Enrich**, for every company:

- **Address → distance.** The Capital IQ "Offices" block is cleaned and
  geocoded (Nominatim, ArcGIS fallback; cached) → miles from UConn Storrs
  (41.8073, −72.2536).
- **Size scores** from revenue and employees (§4).
- **Funding score** — is there money available *now* (§4), from Form D
  raises since 2025, SEC cash and R&D, running SBIR/STTR awards and recent
  university research the company funded.
- **Academic adjustment** — its track record with universities (past SBIR,
  clinical-trial and co-authored research partners), added to the AI
  partnership score at match time.
- **Company profile.** For companies with SBIR/STTR, ClinicalTrials.gov or
  OpenAlex text, an AI-written ~150-word profile combining the Capital IQ
  description with that text (no people's names; cached). Others keep their
  Capital IQ description.
- **Embedding** of the profile, for the shortlist (§3.4).

Outputs `data/companies_enriched.xlsx`, which carries every source column so
the evidence behind each score is visible.

### 3.3 Stage 1 — Matching one faculty member (`match_faculty.py`)

| Step | What happens |
|---|---|
| **Research profile** | Registration fields + top-weighted publication titles + faculty-notes text. |
| **Size tiers** | Small: revenue under $100m or unknown. Mid: $100m–$1bn. Large: $1bn and above. Each tier is ranked separately. Companies with status Acquired, Out of Business or Liquidating are left out of every tab. |
| **Shortlist (Small only)** | Small lists run to tens of thousands, so they are cut by embedding similarity: the **300 companies** closest to the research description (registration form + faculty-notes text; publications are searched separately), plus up to **30 new companies from each separate search** — each of the first 8 keywords and the industry-interests note (registration-form faculty), the publication titles, the faculty-notes text and each named lead's description. **Hospitals, clinics and practices get separate places** (100 + 10 per search), so they never take a company's place. Mid and Large companies are all scored. `--full` scores every small company instead. |
| **Alignment (AI)** | Each company is scored 1–9 against the profile from its name **and** its profile text. The rubric judges first *how* the researcher works (methods, tools) and then *what* they study. Each company is asked **three times**; the middle numeric answer counts (NA only if all three are NA). A company left at NA is not listed on any tab (it stays in All Companies). Cached per profile + company text. |
| **Minimum alignment** | Mid and Large companies need 4 or more to be listed; hospitals and clinics (any tier) need 4 or more. |
| **Preliminary score** | Distance, employees, revenue and alignment (renormalised weights) rank the scored companies. |
| **Partnership (AI)** | The top 100 companies and the top 100 providers per tab by preliminary score get a 1–9 partnership-likelihood score. The prompt includes the company's university track record. Cached per company, so shared across faculty. NA counts as neutral (0.5). |
| **Overall score** | 30% alignment + 25% distance + 15% partnership (+ academic adjustment) + 15% revenue + 10% funding + 5% employees (§4). |
| **Corporate groups** | A parent and its subsidiaries are listed once: within a tab by the highest-ranked member; across the size tabs by the member with the higher alignment score (a tie goes to the larger tier). The others show "Same group as … (listed)". Groups come from the parent named in Capital IQ descriptions and from near-identical names; parent names are compared without legal forms (Ltd, AG, SE, plc …), but "Merck KGaA" stays apart from "Merck & Co.". |
| **Tabs** | Top 50 per size tier, sorted by overall score (`tab_order: overall`; `verdict_first` lists Strong fits first). Hospitals and clinics of every size share one sheet. |
| **Justifications (AI)** | Every listed company gets 2–3 sentences: the specific overlap, then the practical factors. They start with the verdict, are written *about* the researcher (never "my work"), and are checked automatically: a figure or university name not in our data, or first-person wording, sends the answer back once; if it still fails, a plain data-only justification is used. A company with no website on record gets a "check it still operates" note. |
| **Output** | `data/output/<Name> Match <date_time>.xlsx` (§3.7). Faculty Database runs flip `Flag` N → Y. |

**Verdicts.** Strong fit (alignment 7–9), Moderate fit (5–6), Weak fit (1–4),
Fit unclear (NA). The verdict is research fit only; the ranking uses the
overall score, so a nearby, well-funded Weak fit can rank above a distant
Strong fit.

**Organisation type.** Each company is labelled Company, Contract
manufacturer, Hospital / health system or Clinic / care provider from its
name and description, recomputed at match time so a rule fix needs no
re-enrichment. Hospitals and clinics go to the Hospitals & Clinics sheet. An
animal hospital or vet practice is a clinic; a company that sells veterinary
products is a company.

### 3.4 The embedding shortlist: how cosine similarity works

The pre-filter is the one genuinely new mechanism in v2, so it deserves a
plain-language explanation.

An **embedding** converts a piece of text into a long list of numbers
(1,536 of them with `text-embedding-3-small`) — coordinates that place the
text's *meaning* as a point in a high-dimensional space. Texts about
similar things land near each other: "zeolite catalysis" and "fuel
upgrading catalysts" end up close together; "zeolite catalysis" and
"dental insurance" end up far apart.

**Cosine similarity** measures how closely two of these points point in
the same direction: 1.0 means essentially the same meaning, near 0 means
unrelated. Ranking every company by the cosine similarity between its
description embedding and the faculty profile embedding gives a
"most semantically relevant first" ordering of the entire datasource —
computed in a fraction of a second, with no LLM calls.

Why this approach instead of keyword or industry-classification filtering:
keywords are brittle. A catalysis professor's best match might describe
itself as "process technology" or "fuel upgrading" without ever using the
word "catalysis," and a keyword filter would silently drop it. Embeddings
capture semantic closeness — "pyrolysis" lands near "thermochemical
conversion" in vector space even with zero shared words. There is no
industry or keyword filter: the form's sectors are only compared with
Capital IQ's industry for the Sector Match column and the justification.

Cost and storage: company embeddings are computed once in Stage 0 and
cached permanently (keyed by a hash of the text embedded, so an edited
description automatically re-embeds). Each faculty run embeds only its own
search texts (profile, keywords, notes, publication titles), and those are
cached too. The similarity ranking itself is recomputed in memory each run —
it is too cheap to be worth storing.

The shortlist is deliberately **generous** (300 companies by the whole
profile, plus extra places for each separate search — see §3.3) because it
is the funnel's only lossy step: anything it drops can never be recovered
downstream. Cheap steps should over-include; expensive steps
narrow. The cutoff is a config value that can be raised — or set to 0 to
score the entire datasource. With fewer companies than the cutoff (e.g. a
61-company test file), the pre-filter simply doesn't activate.

### 3.5 The "NA problem": how v2 scores small companies

v1 asked the LLM to judge companies from their **name alone**, which meant
the model could only draw on whatever it happened to remember from its
training data. Small and private companies — the majority of a regional
Capital IQ extract — failed this name-recognition test and were stamped
"NA", sinking them in the rankings regardless of actual fit. v2 fixes this
by including the Capital IQ business description in every scoring prompt.

#### What the AI is given

| | v1 (Testing9) | v2 (ours) |
|---|---|---|
| What the AI receives | Professor's criteria + **company name only** | Professor's criteria + company name + **what the company does** (from Capital IQ) |
| Where the AI gets company info | Its memory only — whatever it learned during training | Its memory **+** the description we hand it in the prompt |
| Famous company (e.g. BASF) | Works fine — AI remembers it | Works fine — same (memory still contributes fully) |
| Small company (e.g. 25-person NJ chemical firm) | AI has never heard of it → forced to answer "NA" | AI reads the description → gives a real score with a reason |

#### A concrete example — same company, both prompts

**v1 asks:**
> "How well does *Cerion Technology, Inc.* align with this research?"

The AI thinks: *"Cerion who? Never heard of them. Rule says answer NA."*
→ **NA, alignment counts as 0**

**v2 asks:**
> "How well does *Cerion Technology, Inc. — a nanomaterials manufacturer
> focused on catalysis and coatings — align with this research?"*

The AI thinks: *"Nanomaterials for catalysis, and the professor works on
catalysts — strong fit."* → **8 out of 9, with a written reason**

Note that a high alignment score does not decide the ranking by itself:
alignment is 30% of the overall score, and 45% of the score is pure
arithmetic (distance, revenue, size) that no description can influence.
In test runs, Cerion's 8/9 alignment — the highest in the pool — still
ranked it only 7th overall because of its distance from Storrs.

#### When does "NA" still happen?

| Situation | v1 | v2 |
|---|---|---|
| Company is small but Capital IQ describes it | **NA** (AI doesn't recognize the name) | Real score (AI reads the description) |
| Capital IQ has no description either (just "-") | NA | NA — *correctly*, since there's truly nothing to judge |
| AI returns something malformed/broken | NA | NA (kept as a safety net) |
| What NA costs the company | Alignment counts as 0 → sinks in ranking | Left off every tab (shown only in All Companies) — but now it only happens to genuinely unknowable companies |

Since then the rubric was tightened further: an unrelated company must
score 1–2, not NA, and with three votes per company (§3.3) a company is NA
only when every vote is NA. The NA escape hatch and strict JSON parsing are otherwise deliberately
retained from v1, and an NA company is now left off the tabs rather than
ranked at 0: when there is genuinely nothing to judge, leaving it out is
safer than a hallucinated score.
The difference is that NA is now the exception (blank-description rows)
rather than the default outcome for every company the model hadn't
memorized.

### 3.6 What deliberately carried over from v1

- The alignment and partnership rubric style (1–9 with NA), tightened since
- Address cleaning and geocoding chain (Nominatim → ArcGIS)
- `exp(−miles/400)` distance decay and the employee cap at 1,000
- The preliminary → secondary → final funnel and the 6-component weighting

### 3.7 The output workbook

| Sheet | Contents |
|---|---|
| Researcher's leads | Only when a company named in the faculty notes is found (§3.8); sorted by alignment score. |
| Small - top 50 (pre-filter) | Top 50 small companies. |
| Mid ($100m-$1bn) - top 50 | Up to 50 mid-size companies (alignment ≥ 4). |
| Large ($1bn+) - top 50 | Up to 50 large companies (alignment ≥ 4). |
| Hospitals & Clinics - top 50 | Up to 50 providers of any size (alignment ≥ 4). |
| All Companies | Every company: scores, the three alignment votes, Stage (how far it got), Found By (which search brought it in). |
| Run Info | Profile used, publications used, model, votes, company counts per tier, shortlist counts, weights, tab order, faculty notes used. |

### 3.8 Faculty notes

`data/input/faculty_notes.yaml` adjusts one faculty member's run; anyone not
in it is matched exactly as before (this is tested).

| Field | Effect |
|---|---|
| `extra_research` | Added to the research profile and searched on its own at the shortlist ("researcher's notes"). |
| `distance_full_score_miles` | Full distance score up to N miles; beyond it the usual decay counts from N. |
| `leads` | Companies the researcher named. The companies *named* like each lead (or, if none, those naming it as their parent) are always AI-scored, shown on the **Researcher's leads** sheet, scored with distance 1.0, and judged with the researcher's note added to their profile. Companies the lead acquired are not treated as the lead. A lead not in the export is taken from the lists under `faculty_notes.lead_sources`. Each lead's description is also searched for similar companies ("like lead: …"). |
| `ignore_publications` | Titles containing these words are left out. |

### 3.9 Architecture comparison at a glance

| | v1 (Watcher + ProgramTesting9) | v2 (BridgeAI) |
|---|---|---|
| Input model | One screening file per professor, dropped manually | Shared, enriched company list + registration form, publications, notes |
| Trigger | File-system watcher on a dedicated machine | Command line (`match_faculty.py --faculty` / `--row`) |
| Geocoding | Every company, every run | Once per address, cached |
| Candidate selection | None (input pre-screened, ~60 companies) | Embedding shortlist over thousands of small companies; Mid and Large all scored |
| LLM context | Company name only | Name + description (AI-enriched profile where sources exist) |
| Alignment | One ask | Three asks, middle answer |
| Secondary scores | Recomputed every run | Partnership cached across faculty; funding computed from data |
| Output | Template copy with scattered columns (+ PPT) | Per-faculty workbook: size tabs, providers, leads, All Companies, Run Info |
| State tracking | None | Caches (resume-safe); Flag column for the Faculty Database |
| Configuration | Hardcoded constants, per-OS code forks | Single `config.yaml`, cross-platform |
| Secrets | Hardcoded in source | `.env`, git-ignored |

## 4. Scoring model reference

| Component | Weight | How it is computed |
|---|---|---|
| Alignment | 30% | AI, 1–9 (middle of 3 votes); min–max normalised across the companies scored in the same size tier (hospitals and clinics on their shared sheet are each scaled within their own tier); an NA company is not listed |
| Distance | 25% | `exp(−miles/400)`; unknown address → 0. With `distance_full_score_miles: N`: `exp(−max(0, miles−N)/400)`. Named leads: 1.0 |
| Partnership | 15% | AI, 1–9, normalised across the companies given it; NA → 0.5; then + academic adjustment (bonuses, together at most +0.15: funded university papers +0.10; past SBIR/STTR or clinical-trial university partner +0.05 each; co-wrote papers with a university +0.05; any SBIR/STTR award +0.03. Penalties, together at most −0.10: university partner on a running award or trial −0.05 each), kept within 0–1 |
| Revenue | 15% | Size fit: under $5m → 0.2; $5m–$100m → 0.4; **$100m–$1bn → 1.0**; $1bn → $5bn tapering 1.0 → 0.2; above $5bn → 0.2; no figure → 0.5 |
| Funding | 10% | Money available now, 0–1 from data (no AI): the best of Form D raised since 2025, SEC cash + short-term investments, SEC R&D expense and running SBIR/STTR awards, each on a log scale (a figure below the scale's floor scores down to 0) and, above 0.5, aged towards 0.5 by its year; funding university papers since 2025 → 1.0; no evidence → 0.5, or 0.55 for a company under $100m (or of unknown revenue) that raised private money before |
| Employees | 5% | `min(1, employees / 1000)` |

The preliminary score uses distance, employees, revenue and alignment only,
re-weighted to sum to 1. All weights and thresholds are in `config.yaml`.

## 5. Company enrichment from public datasets

Capital IQ describes companies well but leaves the fields that matter most
for a partnership decision thin or empty: revenue is blank for most private
companies, and nothing says whether a company has worked with a university,
funds research or has money to spend. Five public datasets fill those gaps.

| Dataset | Steps | What it adds |
|---|---|---|
| **SEC Form D** | download quarters → `clean_formd.py` | dated private capital raises |
| **SBIR/STTR** | download `award_data.csv` → `clean_sbir.py` | federal R&D awards (amounts, running or not), STTR university partners, award titles and abstracts |
| **SEC XBRL** | `fetch_sec_xbrl.py` → `clean_xbrl.py` | R&D expense, revenue, cash and short-term investments (public companies) |
| **ClinicalTrials.gov** | `fetch_clinicaltrials.py` → `clean_clinicaltrials.py` | company trials, conditions and treatment types, university partners |
| **OpenAlex** | `fetch_openalex.py`, `fetch_openalex_ties.py` → `clean_openalex.py` | research topics; universities whose papers it funded or co-wrote (counts only) |

`combine_companies.py` joins all of them to the Capital IQ list; a missing
clean file leaves its columns blank, so there is no required order.

#### Matching companies across sources

Every source except XBRL uses one rule (`match_company`):

1. **Same name** — identical once case, spacing, punctuation and a leading
   "The" are ignored, with the same legal form → match. Names of three
   characters or fewer (acronyms) must also be confirmed.
2. **Similar name** — the same only once Holdings / Group / International /
   USA are ignored, or the legal forms differ. A match only if confirmed:
   both have a website → the websites must agree; otherwise → the ZIP codes
   must agree.
3. **Website only** — no name matches, but the website belongs to exactly
   one company in the source (a rename).

What can confirm a similar name depends on the source: SBIR has websites
and ZIPs; Form D has only ZIPs; OpenAlex has only websites; ClinicalTrials.gov
has neither, so only a same name matches. Rule 3 runs for SBIR and OpenAlex.

**XBRL** joins by ticker → SEC CIK, an exact identifier, so no name
comparison is needed; when the ticker gives nothing, the CIK from the
company's Form D match is tried.

## 6. Repository layout

```
NexusAI/                       (project folder; the product is BridgeAI)
├── config.yaml                 every path, weight, cutoff and model
├── requirements.txt, .env.example
├── check_export.py             check the Capital IQ export's columns
├── fetch_*.py                  download a public dataset
├── clean_*.py                  clean a dataset → data/cache/*.csv
├── combine_companies.py        Capital IQ + sources → companies_combined.xlsx
├── enrich_companies.py         Stage 0 → companies_enriched.xlsx
├── run_enrich.sh               combine + enrich in the background (caffeinate)
├── match_faculty.py            Stage 1 → data/output/<Name> Match ….xlsx
├── enrich_funding.py, enrich_sbir.py, enrich_xbrl.py   older source path; not run any
│                               more, but clean_formd.py uses enrich_funding.py's Form D reader
├── export_formd_raw.py, inspect_formd.py               check a Form D figure
├── run_tests.py                546 tests, stubbed AI, no network
├── nexus/                      the shared library
│   ├── settings.py, cache.py, excel_io.py
│   ├── capitaliq_layout.py     columns found by header name
│   ├── geo.py, prefilter.py, scoring.py, funding.py
│   ├── matching.py, groups.py, organisation.py
│   ├── company_profile.py      AI company profiles
│   ├── faculty_registration.py, publications.py, faculty_notes.py
├── tests/
├── docs/DATA_DICTIONARY.md
└── data/                       git-ignored (licensed and internal data)
```

### 6.1 Data file lifecycle

| File | Written by | Behaviour |
|---|---|---|
| `input/*` | **You** | Read-only to the programs (except the Faculty Database Flag cell). |
| `sources/*` | You, or a `fetch_*` script | Downloaded public data. |
| `cache/*` | The clean steps and the AI calls | Grows over time; this is what makes re-runs cheap. Delete a file only to force a rebuild. |
| `companies_combined.xlsx` | `combine_companies.py` | Rebuilt from the raw export + clean files every run. |
| `companies_enriched.xlsx` | `enrich_companies.py` | Rebuilt every run; can never drift from the export. |
| `datasets/<name>/` | You | Saved company lists; also where faculty-notes leads are looked up. |
| `output/*.xlsx` | `match_faculty.py` | Never overwritten — each run writes a new timestamped file. |

## 7. Known limitations

### Coverage and the shortlist

- **Companies outside the Capital IQ screen can never be matched.** The
  industries and regions screened decide who can appear (an ocean-science
  profile screened only on software and energy services missed offshore
  wind, ocean sensors and coastal consulting). Capital IQ's Northeast and
  Midatlantic regions do not include Pennsylvania.
- **The shortlist is the one lossy step.** A strong company that matches only
  one part of a profile, or whose description reads like a product catalogue,
  can rank just outside the 300 + 30-per-search places and is never scored. A
  larger list (e.g. after adding a region) pushes more such companies out.
  Raising `extra_per_search` is the lever.

### Scoring

- **Generic suppliers can be over-scored.** Instrument makers, IT services
  and compute providers sometimes score high because the researcher *could
  use* their products, even without interest in the field.
- **Big companies' descriptions list therapeutic areas or products, not
  methods**, so large pharma can score low for formulation or methods work;
  faculty-notes leads with a note are the remedy for companies a researcher
  names.
- **Votes that return NA are not cached and are asked again on the next
  run**, so a company scored on a single vote can move on or off a list
  between runs.
- **A short profile** (a few keywords, no publications) makes many companies
  look alike; publications or `extra_research` sharpen it.
- With a distance plateau (faculty notes), every company inside the plateau
  has the same distance score, so many companies tie at the cut-off for
  partnership scoring.

### Data quality

- **Publications exports can contain another researcher's papers** under the
  same name (seen when Scopus records are merged); check titles and use
  `ignore_publications`.
- **Old Capital IQ records** (acquired long ago) can still say "Operating";
  no website on record is the warning sign, and justifications say so.
- **The group rule can list a small subsidiary instead of its large parent**
  (e.g. the subsidiary ranks higher), so the parent appears only in All
  Companies.
- **Label gaps.** Some labs and CROs are labelled as clinics and some clinics
  as companies, which only changes the sheet they appear on.
- Matching across sources is exact-after-normalisation, not fuzzy: a
  misspelling or a rename without a shared website is a miss — deliberately,
  because a wrong match attributes another company's money.

### What the funding numbers do and do not mean

- **The funding score asks "is there money now?"**, so only current signals
  count: Form D money raised since 2025, SBIR/STTR awards still running on
  the day `clean_sbir.py` ran, and the latest SEC cash and R&D figures (aged
  by their year). Lifetime totals are kept as columns for reference only.
- **The best single signal counts, not a sum** — a company with both cash in
  the bank and a recent raise has the same answer twice, not a bigger one.
- **Most federal award history is old.** Only a minority of matched
  companies have an award running now, so SBIR contributes far more to the
  university track record than to current money.
- An R&D figure far too small for a large company's revenue is treated as a
  different SEC item and left out rather than scored.
- No evidence is neutral (0.5), never a penalty: a company with no record is
  not necessarily unfunded.

### Coverage gaps in the source data

- Several fields these datasets offer are only partly filled. Where a field
  is missing, it means *unknown*, never *no* — a blank university end date
  does not mean the collaboration ended, and a company with no recorded
  award is not necessarily unfunded.
- Because of that, the "current university partnership" signal can confirm
  a live collaboration but can never rule one out, and will always
  under-report. The historical partnership list is the more complete field.
- The university partner is only recorded on STTR awards, which are a
  minority of federal awards. Its absence says nothing about a company's
  academic ties in general.
- Placeholder values appear where real values should be — literal strings
  standing in for a missing name. These are filtered, but the filter list
  is maintained by hand and a new placeholder would pass through.
- SEC XBRL covers only public companies, which are the ones Capital IQ
  already describes well. It improves the signal on companies already
  known rather than reaching unknown ones.

### Approaches tested and rejected

- **Web search for funding figures.** Structured funding databases are
  paywalled, and search returns confident, relevant-looking results about
  *different* companies — one test asked for a small company's funding and
  the results supplied another firm's round. A dataset fails loudly when a
  company is absent; search fails silently, which at datasource scale
  cannot be caught by hand.
- **Bulk government spending databases.** The full archive requires
  terabytes of storage and a database server; the published subset covers
  only the most recent year, so companies with long award histories read as
  zero. The per-company API remains a viable option and is unexplored.

## 8. Roadmap

**Done** — Stage 0 and Stage 1; five public sources cleaned and combined;
AI company profiles; size tiers and provider sheet; three-vote alignment;
publications; faculty notes (leads, extra text, distance, ignored titles);
separate shortlist places for providers; grounded, verdict-first
justifications.

**Options discussed, not built**

1. Raise the per-search shortlist places from 30 to 60.
2. Tighten the alignment rubric so generic suppliers (compute, IT services,
   general lab tools) score at most 4 unless their description shows work in
   the researcher's field (re-scores all cached alignments).
3. A "Strong fits outside the top 50" sheet for high-alignment companies that
   missed a tab on distance or partnership.
4. Re-ask an alignment until two numeric votes exist.
5. Cap the "like lead" searches (fewer extra companies per lead).
6. Leave finance and holding entities off the Researcher's leads sheet.
7. List a large parent alongside its smaller listed subsidiary.
