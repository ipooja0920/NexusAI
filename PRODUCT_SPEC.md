# NexusAI — Product Specification

**Faculty ↔ Industry Matching Pipeline, v2 architecture**
*University of Connecticut · August 2026*

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
| Company name only sent to the LLM | Small/private companies judged on model memory, often "NA" |
| Hardcoded OS paths (separate Mac/Windows script versions) | Code forked into "SixthTake"/"SeventhTake" variants |
| Results written into scattered template columns (O/P … DO/DR) | Fragile magic-column code; outputs hard to read |
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
- Parse revenue ($USDmm) and employee counts → deterministic component
  scores
- Compute an **embedding** of the business description (used by the
  pre-filter)

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
├── enrich_companies.py      # Stage 0 CLI
├── match_faculty.py         # Stage 1 CLI (--dry-run, --faculty "Name")
├── test_smoke.py            # offline end-to-end test with mocked AI
├── nexus/                   # importable core (future Streamlit UI reuses this)
│   ├── settings.py          # config loader
│   ├── cache.py             # persistent JSON caches
│   ├── geo.py               # address cleaning, geocoding, distance
│   ├── scoring.py           # prompts, parsing, component score math
│   ├── prefilter.py         # embeddings + cosine ranking
│   └── excel_io.py          # all Excel read/write
└── data/                    # git-ignored: datasource, faculty DB, caches, outputs
```

`data/` never reaches GitHub: Capital IQ extracts are licensed and the
faculty list is internal. Only code and config templates are published.

### 4.1 Data file lifecycle

Each file under `data/` has a different update behavior — knowing which is
which prevents surprises:

| File | Written by | Behavior |
|---|---|---|
| `companies_raw.xlsx` | **You** (Capital IQ extract) | Never touched by the programs — read-only input. Changes only when you replace it with a refreshed extract. |
| `companies_enriched.xlsx` | `enrich_companies.py` | **Rebuilt and overwritten on every enrichment run.** It is purely derived data, so the current raw file is always its single source of truth. Nothing of value is lost by overwriting — the expensive ingredients live in the caches. |
| `cache/*.json` (geocodes, embeddings, company scores) | Both programs | **Append-only** — entries are added, never replaced. This is what makes re-runs and datasource refreshes cheap. |
| `Faculty Database.xlsx` | You + `match_faculty.py` | You add/edit rows; the program writes only the Flag cell (`N → Y`) after a successful match. |
| `output/*.xlsx` | `match_faculty.py` | **Never overwritten** — every run creates a new timestamped file. |

When to run what: `enrich_companies.py` is needed **only when
`companies_raw.xlsx` changes**. The day-to-day loop is just
`match_faculty.py`. Running enrichment by accident with an unchanged
datasource is harmless — it rebuilds the enriched file identically in
seconds, entirely from cache, with zero API spend.

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

- LLM scores rely on a single `gpt-5-nano` call without web search;
  obscure companies may score "NA" (excluded from normalization, never
  falsely boosted).
- The embedding pre-filter is the one lossy step: a match invisible in a
  company's description could be dropped before LLM scoring. Mitigated by
  a generous cutoff (300) that is configurable up to "score everything".
- Nominatim geocoding is rate-limited (~1/sec); the first enrichment of a
  large datasource takes hours (once).
- Faculty Database writes fail if the file is open in Excel; the program
  reports this clearly and the flag system makes re-running safe.

## 7. Roadmap

1. **Now** — CLI on a single Mac; one faculty at a time or batch by flag
2. **Next** — datasource expansion (NY); revisit revenue-band policy after
   reviewing first real outputs; optional PPT export if wanted
3. **Later** — dedicated Windows machine or per-user installs (code is
   already cross-platform); Streamlit UI wrapping the `nexus/` modules;
   optional watcher integration for drop-folder automation
