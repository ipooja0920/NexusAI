# NexusAI — Product Specification

**Faculty ↔ Industry Matching Pipeline, v2 architecture**
*University of Connecticut · September 2026*

---

## 1. Purpose

NexusAI identifies companies that are strong candidates for research
partnerships with UConn faculty. Given a faculty member's research profile
and a Capital IQ company universe, it produces a ranked, explained top-20
list of companies per faculty member — scored on research alignment,
partnership propensity, funding capacity, proximity, and company size.

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
there is **one static company datasource** (a Capital IQ extract, refreshed
occasionally) and a **Faculty Database** (one row per professor
with a `Flag` column: `N` = not yet matched, `Y` = matched). The system
matches every flagged professor against the shared universe.

### Guiding principle

> **Never pay twice for work that doesn't change between runs.**

Everything that depends only on the *company* is computed once and cached.
Only work that depends on the *specific professor* runs per matching.

### 3.1 Stage 0 — Datasource enrichment (`enrich_companies.py`)

Run once per datasource refresh. For every company:

- Clean the Capital IQ "Offices" block → geocode (Nominatim, ArcGIS
  fallback) → distance from UConn Storrs (41.8073, −72.2536)
  — *same logic as ProgramTesting9, carried over unchanged*
- Parse revenue ($USDmm) and employee counts → deterministic component
  scores — *same formulas as ProgramTesting9*
- Compute an **embedding** of the business description — i.e. convert the
  description text into a numerical "meaning fingerprint" that lets
  companies be compared to a faculty profile by topic similarity (see
  §3.3 for the full explanation) — *new in v2; used by the pre-filter*

The difference from ProgramTesting9 is not the logic but **when it runs**:
v1 recomputed all of this on every single run; v2 does it once and caches
the results.

Outputs `data/companies_enriched.xlsx`. Geocodes and embeddings persist in
JSON caches (`data/cache/`), so a datasource refresh only pays for new or
changed companies; failures are cached too so bad addresses aren't
retried forever.

### 3.2 Stage 1 — Matching run (`match_faculty.py`)

For each faculty row with `Flag = N`:

| Step | What happens | Why |
|---|---|---|
| Build research profile | Research Description + Department + Industry Classifications | Richer context than v1's bare criteria paragraph |
| **Embedding pre-filter** | Rank all companies by cosine similarity to the profile; keep top 300 (configurable) | Cuts thousands of companies to a relevant pool for ~1 API call; v1 had no equivalent because its input was pre-screened |
| Alignment scoring | v1's 1–9 rubric prompt, **now including the company's business description** | Small companies judged on what they do, not on model memory of their name |
| Funnel to top 40 | Preliminary score from distance/employees/revenue/alignment | Secondary scoring spent only where it can affect the top 20 |
| Partnership + funding scoring | Same prompts as v1, but results are **cached across faculty** (faculty-independent) | "Does company X fund academic research?" has the same answer for every professor — pay once |
| Final weighted score | 0.25 distance + 0.05 employees + 0.15 revenue + 0.30 alignment + 0.15 partnership + 0.10 funding | Same weights as v1; all configurable |
| Output | Clean self-contained workbook: a ranked **Top Matches** sheet, an **All Companies** sheet showing every company's scores and the funnel stage it reached (Top 20 / secondary scored / alignment scored / pre-filtered out), plus a **Run Info** sheet (profile, model, weights, date) | Keeps v1's full-list visibility but adds the rank, overall score, and stage labels v1 never wrote to the file |
| Flag flip | `N → Y` in the Faculty Database | Idempotence + crash recovery + zero extra bookkeeping |

### 3.3 The embedding pre-filter: how cosine similarity works

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
conversion" in vector space even with zero shared words. Classification
keyword filtering remains available as an optional extra layer, not the
primary mechanism.

Cost and storage: company embeddings are computed once in Stage 0 and
cached permanently (keyed by a hash of the description text, so an edited
description automatically re-embeds). Each faculty run costs exactly one
additional embedding call for the profile. The similarity ranking itself
is recomputed in memory each run — it is too cheap to be worth storing.

The pre-filter is deliberately **generous** (top 300 by default) because
it is the funnel's only lossy step: anything it drops can never be
recovered downstream. Cheap steps should over-include; expensive steps
narrow. The cutoff is a config value that can be raised — or set to 0 to
score the entire datasource. With fewer companies than the cutoff (e.g. a
61-company test file), the pre-filter simply doesn't activate.

### 3.4 The "NA problem": how v2 scores small companies

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
| What NA costs the company | Alignment counts as 0 → sinks in ranking | Same — but now it only happens to genuinely unknowable companies |

The NA escape hatch, strict JSON parsing, and NA → 0 normalization are
deliberately retained from v1: when there is genuinely nothing to judge,
an honest NA that sinks in the ranking is safer than a hallucinated score.
The difference is that NA is now the exception (blank-description rows)
rather than the default outcome for every company the model hadn't
memorized.

### 3.5 What deliberately carried over from v1

- The alignment / partnership / funding **prompt rubrics** (proven)
- Address cleaning and geocoding chain (Nominatim → ArcGIS)
- Component score formulas: `exp(−miles/400)` distance decay, employee
  cap at 1,000, revenue band (1.0 between $30–60mm → 0 at $1B, missing → 0.5)
- The 40 → 20 funnel shape and the 6-component weighting

### 3.6 Architecture comparison at a glance

| | v1 (Watcher + ProgramTesting9) | v2 (NexusAI matching) |
|---|---|---|
| Input model | One screening file per professor, dropped manually | Static company universe + faculty database with flags |
| Trigger | File-system watcher on a dedicated machine | CLI run (watcher/UI can wrap it later) |
| Geocoding | Every company, every run | Once per company, cached forever |
| Candidate selection | None (input pre-screened, ~60 companies) | Embedding pre-filter over thousands of companies |
| LLM context | Company name only | Name + business description |
| Secondary scores | Recomputed every run | Cached across all faculty runs |
| Output | Template copy with scattered columns (+ PPT) | Clean per-faculty workbook + Run Info sheet |
| State tracking | None | Flag column (resume-safe, idempotent) |
| Configuration | Hardcoded constants, per-OS code forks | Single `config.yaml`, cross-platform (pathlib) |
| Secrets | Hardcoded in source | `.env`, git-ignored |
| Cost per professor | ~60 geocodes + ~140 LLM calls, all fresh | ~1 embedding + ≤300 alignment calls + cached secondaries; shrinks over time |

## 4. Repository layout

```
NexusAI/
├── config.yaml              # all paths, weights, cutoffs, models
├── requirements.txt         # pinned dependencies (Python 3.10+)
├── .env.example             # template for OPENAI_API_KEY (real .env git-ignored)
│
├── enrich_companies.py      # Stage 0 — builds companies_enriched.xlsx
├── enrich_funding.py        # source step — SEC Form D
├── enrich_sbir.py           # source step — SBIR/STTR awards
├── fetch_openalex.py        # source step — OpenAlex research record
├── fetch_sec_xbrl.py        # source step — R&D expense from SEC XBRL
├── match_faculty.py         # Stage 1 — faculty → ranked companies
├── run_tests.py             # 137 tests, ~5s, no API key or network needed
│
├── nexus/                   # importable core (a future UI reuses this)
│   ├── settings.py          # config loader
│   ├── cache.py             # persistent JSON caches
│   ├── geo.py               # address cleaning, geocoding, distance
│   ├── matching.py          # company-name matching, shared by every source
│   ├── scoring.py           # prompts, parsing, component score math
│   ├── prefilter.py         # embeddings + cosine ranking
│   └── excel_io.py          # all Excel read/write
├── tests/
└── data/                    # git-ignored
    ├── input/               # files you provide
    ├── sources/             # downloaded reference data
    ├── cache/               # things we computed
    ├── review/              # match-quality workbooks
    ├── output/              # per-faculty results
    └── companies_enriched.xlsx
```

`data/` never reaches GitHub: Capital IQ extracts are licensed and the
faculty list is internal.

Each folder under `data/` holds one kind of file. The test for where
something belongs: **if you deleted it, would you re-download it or
re-compute it?** Downloads go in `sources/`, computed results in `cache/`.
That split means `cache/` can be wiped for a clean rebuild without losing a
download, and `sources/` cleared to reclaim disk without losing computed
work.

### 4.1 Data file lifecycle

| File | Written by | Behaviour |
|---|---|---|
| `input/companies_raw.xlsx` | **You** (Capital IQ extract) | Read-only to the programs. Changes only when you replace it. |
| `input/Faculty Database.xlsx` | You + `match_faculty.py` | You add rows; the program writes only the Flag cell (`N → Y`). |
| `sources/*` | **You**, or a fetch script | Downloaded public data. Never edited. |
| `cache/*.json` | The source steps | Append-only. This is what makes re-runs cheap. |
| `companies_enriched.xlsx` | `enrich_companies.py` | **Rebuilt from raw + caches every run.** Purely derived, so it can never drift from the datasource. |
| `review/*.xlsx` | The source steps | Overwritten each run. For checking match quality by eye. |
| `output/*.xlsx` | `match_faculty.py` | Never overwritten — every run writes a new timestamped file. |

`enrich_companies.py` is needed only when `companies_raw.xlsx` changes, or
after a source step has refreshed a cache. Running it unnecessarily is
harmless: it rebuilds identically in seconds, entirely from cache, with no
API spend.

### 4.2 Company enrichment from public datasets

Capital IQ describes companies well but leaves the fields that matter most
for a partnership decision thin or empty. Revenue is blank for most private
companies. Employee counts are often missing. Nothing in it says whether a
company has ever funded academic research, worked with a university, or
spends anything on R&D.

So before faculty matching runs, the company dataset is enriched from
public datasets that fill those gaps. This happens in two parts:

1. **Source steps** — one script per dataset. Each reads its dataset,
   matches it to our companies, and writes what it found to `data/cache/`.
   They run independently and on their own schedules.
2. **Assembly** — `enrich_companies.py` reads the raw datasource plus every
   cache and rebuilds `companies_enriched.xlsx`. It is the only thing that
   writes that file.

A source step never writes the enriched workbook, and the workbook is
always a full rebuild rather than a merge, so it cannot drift from the
datasource or keep stale rows. If a cache does not exist yet, its columns
come out blank and nothing breaks — there is no required run order.

#### The datasets

| Dataset | What it covers | What it adds |
|---|---|---|
| **SEC Form D** | any US company raising private capital, in any sector | dated amounts raised, from the company's own filing |
| **SBIR/STTR** | federal R&D awards to small businesses, all agencies | dated award amounts, agency, award phase, employee counts, and the university partner named on each STTR award |
| **SEC XBRL company facts** | reported financials for any public company, whatever the industry | R&D expense — research intensity, which revenue does not show |
| **OpenAlex** | published research and research funders worldwide | whether a company publishes research or has funded academic work |

The first three are complementary rather than overlapping. Form D covers
companies raising private capital; SBIR covers companies the government
pays to do research — and grant money is not a securities offering, so a
company living on federal awards files no Form D at all. XBRL covers only
public companies, which are the ones Capital IQ already describes well, so
it adds a different signal rather than more coverage.

#### Matching companies across sources

Every source joins to the Capital IQ list through `nexus/matching.py`, so
the rule is written once and improving it improves all of them.

Names are normalised before comparison — lowercased, punctuation and legal
suffixes stripped — so the same company spelled several ways collapses to
one key. An exact match on that key is the starting point, never the
conclusion: company names are not unique.

What settles it is the **website**. A domain is a registered identifier a
company keeps through a relocation and usually through a rename:

- website agrees → same company, whatever the address says
- website disagrees → different companies
- nothing to compare → the name match stands on its own

**A differing address is not evidence.** Sources record the address as at
the time of the record and never revisit it, so any company that has moved
will disagree with its own older records. An earlier version rejected
matches on this and discarded a substantial number of real companies.

#### What each dataset offers for matching

Not every dataset carries the same identifying fields, so the same rule
does different amounts of work depending on the source:

| Dataset | Name | Address | Website | How it is matched | Built? |
|---|---|---|---|---|---|
| **SEC Form D** | ✓ | ✓ | — | name only, corroborated by state | yes |
| **SBIR/STTR** | ✓ | ✓ | partly filled | name, falling back to website | yes |
| **SEC XBRL** | ✓ | — | — | ticker → CIK, an exact identifier | **downloaded only** |
| **OpenAlex** | ✓ | region only | homepage | name, falling back to website | **not yet** |

The script names record this: `enrich_*` scripts match a dataset against
the datasource and write a cache; `fetch_*` scripts only download. XBRL and
OpenAlex are currently fetch-only, so their data sits in `data/sources/`
with nothing joining it to our companies yet.

Two consequences worth being explicit about:

**Form D is matched on name alone.** Its filings carry an address but no
website, so there is no domain to corroborate a match or to fall back on.
A company that renamed itself between the filing and the datasource is
simply a miss there — where in a dataset with websites it would still be
found. Form D does publish previous-name fields, but those record legal
rather than business continuity and are deliberately not used (see §6).

**SEC XBRL will not use this matcher at all.** Public companies carry a
ticker in the Capital IQ name, and SEC publishes a ticker-to-CIK map, so
the join is an exact identifier lookup with no name comparison and no
ambiguity. That is the strongest form of matching available, and it is
possible only because both sides share a registered identifier — which is
exactly what private companies lack. The download and the ticker map are in
place; the join itself is not written yet.

## 5. Scoring model reference

- **Alignment (30%)** — LLM, 1–9 rubric against the research profile,
  min–max normalized across the candidate pool
- **Distance (25%)** — `exp(−miles/400)`; unknown address → 0
- **Partnership (15%)** — LLM, 1–9, normalized within the top 40
- **Revenue (15%)** — band score: 1.0 in $30–60mm, → 0 at $1B, 0.5 if missing
  *(encodes a deliberate preference for reachable mid-size partners; tunable)*
- **Funding (10%)** — LLM, 1–9, normalized within the top 40
- **Employees (5%)** — `min(1, employees/1000)`

All weights and thresholds are `config.yaml` values.

## 6. Known limitations

### Matching companies between datasets

- Matching is exact-after-normalisation, not fuzzy. A misspelling, or a
  rename with no shared website, is a miss. This is deliberate: a wrong
  match silently attributes another company's money, which is worse than
  missing one.
- A small number of companies cannot be resolved either way — typically
  very short or initial-based names, where two unrelated companies can
  plausibly share a name and nothing else is available to separate them.
  These are excluded from the caches but kept in the review workbooks.
- Duplicate companies in the datasource — the same firm listed under two
  names — match the same external record. That is correct, but both rows
  can appear in one faculty's results.
- **Address is not used as a fallback**, though it would add matches.
  Companies cluster in shared incubators and office parks, so a single
  address routinely hosts many unrelated firms; matching on it would hand a
  company its neighbours' funding.
- **Previous-name fields are not used as a fallback**, though Form D
  provides them. They record legal continuity, not business continuity: a
  shell company can be taken over and change business entirely while
  keeping its filing history. Where both the old and new names are in the
  datasource, following them would attribute one raise to two companies.

### What the funding numbers do and do not mean

- **A lifetime total is not spending capacity.** Money awarded decades ago
  was spent decades ago. The `_recent_usd` columns represent current
  capacity; the `_total_usd` columns are reference and audit only.
- **Most federal award history is old.** Only a minority of matched
  companies have won an award recently. This data contributes far more to
  track record than to current capacity.
- **The combined funding figure mixes two kinds of dollar.** A federal
  grant is reimbursement for work on a specific project; an equity raise is
  cash in the bank. The combined number is coarse by nature — acceptable
  for a low-weighted score, but the per-source columns are what to trust.
- Award amounts are booked in the year the paperwork lands, not the year
  the money is spent. Multi-year contracts therefore look lumpy, which is
  why recency uses a multi-year window rather than the latest year alone.

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
- Where the datasource is screened to a single industry, industry-based
  logic cannot be tuned or tested. The agency-to-industry mapping is built
  and ready but unexercised until the datasource spans more sectors.

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

### Scoring and pipeline

- LLM scores use a single small-model call with no web search; obscure
  companies may score "NA", which is excluded from normalisation rather
  than treated as a low score.
- The embedding pre-filter is the one lossy step: a match invisible in a
  company's description can be dropped before LLM scoring. Mitigated by a
  generous cutoff, configurable up to "score everything".
- Award titles and abstracts are collected but **not** yet used to enrich
  company descriptions. Doing so would change the text the pre-filter
  embeds, and therefore which companies reach the LLM for every faculty
  member — a change to matching behaviour rather than an added column, and
  it needs its own measurement before being adopted.
- Geocoding is rate-limited, so the first enrichment of a large datasource
  takes hours. Once — after that it is cached.
- Writes fail if the target file is open in Excel; the program says so
  plainly and the caches make re-running free.

## 7. Roadmap

**Done** — Stage 0 and Stage 1; Form D, SBIR and SEC XBRL readers; shared
company matching.

**Next**

1. **Match SEC XBRL** to the datasource by ticker → CIK, writing a cache and
   a review sheet like the other sources. The data is downloaded but
   nothing joins it to our companies.
2. **Match OpenAlex** the same way — also downloaded, also unjoined.
3. **Join every cache into `companies_enriched.xlsx`** as columns. This is
   the step that makes any of the enrichment visible to matching.
4. **Decide how funding feeds the score.** It should probably not be linear
   in dollars: the revenue component already scores a billion-dollar
   company at zero, on the view that it is too large to care about a UConn
   partnership, and funding capacity likely wants the same shape.

**Later**

- Enrich company descriptions from SBIR award titles. Measure the effect on
  a real faculty's rankings first — it changes the pre-filter, not just a
  column.
- Extend the Form D download back beyond 2023.
- Datasource expansion beyond one industry, which is also what makes the
  agency-to-industry mapping testable.
- Windows machine or per-user installs (the code is already
  cross-platform); a UI wrapping the `nexus/` modules.
