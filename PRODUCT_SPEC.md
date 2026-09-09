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
there is **one static company datasource** (Capital IQ extract for CT/MA/RI,
refreshed occasionally) and a **Faculty Database** (one row per professor
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

### 4.2 Funding and track-record sources

Capital IQ leaves revenue blank for **1,762 of 2,477** companies — almost
all of them private. Four public datasets fill parts of that gap. Each has
its own reader, its own cache and its own review sheet, so they refresh
independently and a problem in one never forces a rebuild of the others.

| Source | Answers | Reach |
|---|---|---|
| **SEC Form D** | did investors put private capital in, how much, when | 325 companies, 225 with no CIQ revenue |
| **SBIR/STTR** | did the federal government pay them to do research | 391 companies, 287 with no CIQ revenue |
| **SEC XBRL** | how much do they spend on R&D | ~176 public companies |
| **OpenAlex** | do they publish research or fund academic work | not yet matched |

Form D and SBIR barely overlap — only 65 companies match both — because
grant money is not a securities offering, so a company living on SBIR
awards files no Form D at all. Together they cover **452 of the 1,762
companies Capital IQ knows nothing about (26%)**.

#### Matching companies across sources

Every source joins to the Capital IQ list through `nexus/matching.py`, so
the rule is written once and improving it improves all of them.

Names are normalised before comparison — lowercased, punctuation and legal
suffixes stripped — so `AERODYNE RESEARCH INC` and `Aerodyne Research, Inc.`
both become `aerodyne research`. An exact match on that key is the starting
point, never the conclusion: company names are not unique.

What settles it is the **website**. A domain is a registered identifier a
company keeps through a move and a rename, so:

- website agrees → same company, whatever the address says
- website disagrees → different companies
- nothing to compare → the name match stands on its own

**A differing address is not evidence.** Sources record the address as at
the time of the record — SBIR stamps a 1998 award with a 1998 address and
never revisits it. Alexion is in Massachusetts and its older records say
Connecticut because it moved after the last one. An earlier version rejected
on this and discarded 35 real companies.

SBIR also falls back to matching on website when the name does not match,
which finds eight companies that renamed themselves — Antigenics became
Agenus, ProterixBio was BioScale, KALA BIO was Kala Pharmaceuticals.

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

### Matching

- Matching is exact-after-normalisation, not fuzzy. A misspelling, or a
  rename with no shared website, is a miss. This is deliberate: a wrong
  match silently attributes another company's money, which is worse.
- **Three companies are excluded** as unresolvable: `Casimir, LLC`
  (casimirtrials.com here, casimirspace.com in SBIR — different companies
  sharing a name), and `Imt, LLC` and `EMD Holding Corp.`, three-letter
  names with nothing to corroborate them.
- The datasource contains roughly ten duplicate companies — the same firm
  under two names, e.g. `Abbratech, Inc.` and `AbbraTech Holdings LLC`. They
  match the same external record, which is correct, but both appear in a
  faculty's results.
- **No address fallback**, though it would add 215 matches. 2,035 of the
  25,094 addresses in the SBIR file host more than one company (one hosts
  32) because biotech firms cluster in shared incubators. It would hand a
  company its neighbours' grants.
- **No previous-name fallback for Form D**, though the file offers six such
  columns and they would add 22 matches. Those record legal continuity, not
  business continuity: most are reverse mergers where a shell takes on a
  different business. Six have both names in the datasource (Arsanis and X4
  Pharmaceuticals), so following them would attribute one raise to two
  companies.

### Funding data

- **A lifetime total is not spending capacity.** Inotek Pharmaceuticals
  shows $38.4m of SBIR awards, all of it between 1997 and 2005 and long
  spent. The `_recent_usd` columns (5-year window) represent capacity; the
  `_total_usd` columns are reference only.
- **Most SBIR history is old.** Only 140 of 391 matched companies have an
  award in the last five years; 140 last won one before 2015. SBIR
  contributes far more to track record than to current capacity.
- **`funding_total_usd` mixes two kinds of dollar.** A federal grant is
  reimbursement for work on a specific project; a Series A is equity in the
  bank. The combined figure is coarse by nature — fine for a 0.10 weight,
  but the per-source columns are what to trust.
- **The Form D window starts in 2023** because that is what was downloaded,
  not a limit of the data. Rounds before then are missing — Centrexion's
  $67m Series D, for example. The SEC publishes quarters back to 2009.

### Coverage gaps

- **`university_current` names only 2 companies.** `Contract End Date` is
  filled for 49% of awards, and most university ties in the data ended
  between 2016 and 2024. The column can say *yes* confidently but never
  *no*. `university_past` (83 companies) is the useful one.
- `RI Name` is filled for only 9.8% of awards once placeholders are
  removed — the literal string `Stub` accounts for 41% of that column. A
  blank means "no STTR award", not "no university ties".
- SBIR fills `Company Website` for 55.9% of awards, so the website check —
  the strongest signal available — cannot run on nearly half the data.
- **SEC XBRL reaches ~176 companies, nearly all of which already have
  revenue.** It adds a better signal (research intensity) on companies
  already known, not coverage of unknown ones.
- The Capital IQ export is screened to a single industry, so the industry
  column is constant across all 2,477 rows. The agency-to-industry mapping
  in `enrich_sbir.py` is built but cannot be tuned until the datasource
  spans more industries.

### Deliberately not used

- **Web search for funding figures.** Tested on four companies: structured
  funding databases are paywalled, and search returns confident,
  relevant-looking results about *different* companies. Asked how much
  Pelias Technologies raised, the results offered Egalet's $27.6m. A
  dataset fails loudly when a company is absent; search fails silently.
- **The USAspending database subset.** Its award table covers 2025–2026
  only, and the per-recipient totals are last-12-months, so companies with
  decades of federal awards read as $0.

### Scoring and pipeline

- LLM scores use a single `gpt-5-nano` call with no web search; obscure
  companies may score "NA" (excluded from normalisation, never falsely
  boosted).
- The embedding pre-filter is the one lossy step: a match invisible in a
  company's description can be dropped before LLM scoring. Mitigated by a
  generous cutoff (300), configurable up to "score everything".
- Award titles and abstracts are collected but **not** used to enrich
  company descriptions. Doing so would change `company_embedding_text()`
  and therefore which companies reach the LLM for every faculty member —
  a matching change, not an additive one, and it needs its own measurement.
- Nominatim geocoding is rate-limited (~1/sec), so the first enrichment of
  a large datasource takes hours. Once.
- Writes fail if the target file is open in Excel; the program says so
  plainly and the caches make re-running free.

## 7. Roadmap

**Done** — Stage 0 and Stage 1; Form D, SBIR and SEC XBRL readers; shared
company matching.

**Next**

1. Join the funding caches into `companies_enriched.xlsx` as columns. The
   caches exist; nothing reads them yet.
2. Match OpenAlex against the datasource — fetched, not yet joined.
3. Decide how funding feeds the score. It should probably not be linear in
   dollars: the revenue component already scores a billion-dollar company
   at zero, on the view that it is too large to care about a UConn
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
