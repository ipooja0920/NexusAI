# NexusAI — Faculty ↔ Company Matching

Matches UConn faculty research profiles against a Capital IQ company
datasource and produces a ranked top-20 workbook per faculty member.

See **[PRODUCT_SPEC.md](PRODUCT_SPEC.md)** for the full architecture, how
it differs from the previous watcher/monster-program system, and the
scoring model reference.

## Project layout

```
NexusAI/
├── config.yaml               every path, weight, cutoff and model name
├── requirements.txt
├── .env                      your OpenAI key (git-ignored, never commit)
├── .env.example
│
├── enrich_companies.py       STAGE 0 — builds companies_enriched.xlsx
├── enrich_funding.py         source step — SEC Form D  → cache
├── fetch_openalex.py         source step — OpenAlex    → sources/
├── match_faculty.py          STAGE 1 — faculty → ranked companies
├── run_tests.py              93 tests, ~5s, no API key needed
│
├── nexus/                    the shared library
│   ├── settings.py             config loader, path resolution
│   ├── excel_io.py             read/write every workbook
│   ├── geo.py                  address cleaning, geocoding, distance
│   ├── prefilter.py            embeddings + cosine similarity
│   ├── scoring.py              LLM prompts + component score maths
│   ├── matching.py             company-name normaliser (all sources)
│   └── cache.py                the JSON caches
│
├── tests/                    each test builds its own throwaway project
│
└── data/                     git-ignored — see below
    ├── input/                  files YOU provide
    ├── sources/                downloaded from outside
    ├── cache/                  computed by us
    ├── review/                 match-quality workbooks
    ├── output/                 final per-faculty results
    └── companies_enriched.xlsx
```

Only `data/` and `.env` stay off GitHub. Everything else is code and is
safe to clone, zip, or hand to someone else.

## How it works

**Stage 0 — `enrich_companies.py`** (run once, and again only when you
refresh the Capital IQ extract): cleans every company record, geocodes the
address → distance from UConn Storrs, parses revenue/employees into
component scores, and computes a description embedding. Results are written
to `data/companies_enriched.xlsx`; geocodes and embeddings are cached in
`data/cache/` so a datasource refresh only pays for what changed.

**Source steps — `enrich_funding.py`, `fetch_openalex.py`** (run on their own
schedule, whenever you refresh a download): each reads one external dataset,
matches it to your companies by name, and writes the result to `data/cache/`.
They never touch the enriched workbook — Stage 0 is the only thing that
writes it, and picks these caches up on its next run. If a cache doesn't
exist yet, those columns just come out blank; nothing breaks and no run
order is required.

| Script | Reads | Answers |
|---|---|---|
| `enrich_funding.py` | `sources/formd/` | has this company raised private capital, how much, how recently? |
| `fetch_openalex.py` | OpenAlex API | does it publish research or fund academic work? |

**Stage 1 — `match_faculty.py`** (run whenever faculty have `Flag = N`):
for each unmatched faculty, builds a research profile, pre-filters the
company universe by embedding similarity (top 300 by default), scores each
candidate with the LLM alignment rubric (1–9), takes the top 40 for
partnership + funding scoring (cached across faculty), computes the final
weighted score, writes `data/output/<Faculty> Match <timestamp>.xlsx`, and
flips the faculty's Flag to `Y`.

## One-time setup

```bash
# 1. Create a virtual environment  (Windows: python -m venv venv)
python3 -m venv venv

# 2. Activate it                   (Windows: venv\Scripts\activate)
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add your OpenAI key
cp .env.example .env      # then edit .env and paste your real key
```

Requires Python 3.10+ (`python3 --version` to check).

## The `data/` folder

`data/` is git-ignored — Capital IQ extracts and faculty lists never go to
GitHub. Each subfolder holds one *kind* of file, which is what makes it
obvious where a new file belongs and what is safe to delete:

```
data/
├── input/                    files YOU provide       ← never written by the programs
│   ├── companies_raw.xlsx
│   └── Faculty Database.xlsx
├── sources/                  downloaded from outside ← re-downloadable
│   ├── formd/                  SEC Form D quarters
│   └── openalex/               raw API pulls + openalex_us.xlsx
├── cache/                    computed by us          ← delete to force a rebuild
│   ├── geocode.json
│   ├── embeddings.json
│   ├── funding_formd.json
│   └── company_scores.json
├── review/                   match-quality workbooks, to check by eye
├── output/                   the final per-faculty results
└── companies_enriched.xlsx   the hand-off between enrichment and matching
```

**How to decide where a new file goes:** if you deleted it, would you
*re-download* it or *re-compute* it? Downloads go in `sources/`, computed
results in `cache/`. That split is what lets you wipe all of `cache/` to
force a clean rebuild without losing a single download, and clear `sources/`
to reclaim disk without losing computed work.

`companies_enriched.xlsx` sits at the top rather than in a subfolder because
it belongs to neither stage — Stage 0 builds it, Stage 1 consumes it.

On a fresh clone, create `data/input/` and add these two files:

| File | What it is |
|---|---|
| `data/input/companies_raw.xlsx` | Capital IQ screening export (headers on row 8, data from row 9; columns A=name, C=employees, D=revenue $USDmm, G=address, I=description, J=website) |
| `data/input/Faculty Database.xlsx` | Headers on row 1: Faculty, School/College, Department, Research Description, Industry Classification1, Industry Classification2, Flag (N = not yet matched) |

## Where to download each source

Everything in `data/sources/` is public data you fetch yourself. This section
is the record of *where from*, so a refresh a year from now doesn't start with
hunting for the right page.

### SEC Form D — private capital raises

**Download from:**
<https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets>

Or navigate there from the top of <https://www.sec.gov>:

```
Data & Research  →  SEC & Markets Data  →  Form D Data Sets
```

(The URL mirrors that path, so if the menu is reorganised the address above
still tells you where it used to live.)

Form D is the notice a company files when it raises private capital without
registering with the SEC. It gives a dated dollar amount from the company's
own filing, and it covers exactly the young private companies Capital IQ
has no revenue for.

**SEC posts a new quarter every quarter**, so this page keeps growing — a
quarter you downloaded is final and never changes, but there is always a
newer one to add. Their stated rule: anything filed after 5:30pm Eastern on
the last business day of a quarter lands in the *next* quarterly posting. So
after a quarter closes, give it a few weeks before expecting the file.

To stay current you only ever download the quarters you don't already have;
you never re-download the old ones.

SEC ships this **one .zip per quarter**, so you pick your history at download
time — grab every quarter back as far as you want to go. Put each one in its
own folder:

```
data/sources/formd/
├── 2023Q1_d/          ← unzipped folder, or leave it as 2023q1.zip
├── 2023Q2_d/
└── ...                  both forms work; the folder names don't matter
```

The reader scans the folder for anything that looks like a quarter, so adding
one later is just dropping it in and re-running. Only three files inside each
quarter are used — `ISSUERS.tsv`, `OFFERING.tsv`, `FORMDSUBMISSION.tsv` — but
leaving the rest in place is harmless.

> Note: <https://www.sec.gov/data-research/final-data-quality-assurance-guidelines>
> is **not** this. That page is the SEC's 2002 information-quality policy —
> no datasets on it. Easy to land on by mistake when searching sec.gov.

### SBIR / STTR — federal R&D awards

**Download from:** <https://www.sbir.gov/awards>

Grant money is not a securities offering, so a company living on SBIR awards
files no Form D at all. That makes this the complement to Form D rather than
an overlap — same reason both are worth having.

Scroll past the search filters to this line on the page:

> "Download all SBIR.gov award data either with **award abstracts** (290MB)
> or without award abstracts (65MB)."

Click **award abstracts** — direct link:
<https://data.www.sbir.gov/mod_awarddatapublic/award_data.csv>

Save it as `data/sources/sbir/award_data.csv`.

The abstracts are why we take the larger file. They describe the actual
research each award funded, which for a tiny R&D company is far richer than
its one-line Capital IQ description. Without-abstracts
(<https://data.www.sbir.gov/mod_awarddatapublic_no_abstract/award_data_no_abstract.csv>,
65MB) carries the same dollar amounts and dates if you only want the funding
signal.

**Ignore the year filter on the website.** The page lets you filter by year,
agency, phase, program and state before downloading — but take the whole file
anyway and narrow at read time with `--min-year`. Unlike Form D, SBIR is a
single CSV covering every award since 1983, so history is free here. Filtering
at download time just means re-downloading when you change your mind.

There is also an Award API at <https://www.sbir.gov/api>. Don't use it: it was
under maintenance as of September 2026, it exposes fewer fields than the bulk
file, and 200k awards over the network is slower than one file you re-read for
free.

SBIR refreshes the file monthly.

### OpenAlex — research and funding track record

No manual download — `fetch_openalex.py` pulls it from the API into
`data/sources/openalex/`. An API key is optional but raises the daily budget
from $0.10 to $1; get one free at <https://openalex.org/settings/api> and add
it to `.env` as `OPENALEX_API_KEY=...`.

### Three rules about the company datasource

**1. The filename must match `config.yaml` exactly — treat it as
case-sensitive.** The name isn't hardcoded anywhere; the only thing that
decides it is this line:

```yaml
paths:
  companies_raw: data/input/companies_raw.xlsx
```

`Companies_raw.xlsx` (capital C) fails on Linux and may or may not work on
macOS depending on how the disk was formatted. Don't rely on it — match
the name exactly, or change the config line to match your file.

**2. To refresh the datasource, overwrite the file — keep the same name.**
The programs only ever *read* `companies_raw.xlsx`; they never write or
delete it, so replacing it is your action. Same name means `config.yaml`
never has to change, and nothing gets forgotten on the next refresh.

**3. Never delete `companies_enriched.xlsx`.** It is rebuilt and
overwritten on every enrichment run. There is no situation where deleting
it first helps.

> **The one trap:** if you add the new export under a *different* name
> (`companies_raw_v2.xlsx`), both files sit in the folder and the program
> keeps reading the old one — with no error, because the configured file
> still exists. Stage 0 prints the path and company count it read, so
> check those two lines after a refresh:
>
> ```
> Reading Capital IQ export: .../data/input/companies_raw.xlsx
> Found 2478 companies.
> ```

## Running

```bash
source venv/bin/activate            # (each new terminal session)

python enrich_companies.py          # after adding/refreshing the datasource
python match_faculty.py             # match everyone with Flag = N
```

After adding a new download to `data/sources/`, run its source step, then
Stage 0 again to fold the result into the enriched workbook:

```bash
python enrich_funding.py            # after adding SEC quarters to sources/formd/
python fetch_openalex.py            # downloads to sources/openalex/ (~200MB)
python enrich_companies.py          # rebuild — free, everything else is cached
```

Useful options:

```bash
python match_faculty.py --dry-run             # show what would run, no API spend
python match_faculty.py --faculty "Ioulia Valla"   # force one faculty, ignore flag
python enrich_companies.py --skip-geocode     # skip geocoding (testing only)
python enrich_funding.py --min-year 2023      # ignore older Form D filings
python fetch_openalex.py --refresh            # refetch instead of reusing sources/
```

You only need `enrich_companies.py` when the company datasource changes.
Day to day, `match_faculty.py` is the whole workflow.

## Which files do I have to manage?

Almost none — the programs look after their own files.

| File | Do you ever touch it? |
|---|---|
| `data/input/companies_raw.xlsx` | **Yes** — you replace it when you refresh the Capital IQ extract. Read-only to the programs. |
| `data/input/Faculty Database.xlsx` | **Yes** — you add faculty rows. The program only writes the Flag cell (`N` → `Y`). |
| `data/sources/formd/` | **Yes** — you drop each downloaded SEC quarter in here. Nothing else needs changing. |
| `data/sources/openalex/` | No — written by `fetch_openalex.py`. |
| `data/companies_enriched.xlsx` | No — rebuilt and overwritten on every enrichment run. Never needs deleting. |
| `data/cache/*.json` | No — grows on its own. Delete only to deliberately force re-geocoding or re-scoring. |
| `data/review/*.xlsx` | Read them — that's their whole purpose. Overwritten on each run. |
| `data/output/*.xlsx` | Optional — each run writes a new timestamped file, so these accumulate. Tidy up old ones if you like. |

If a file is open in Excel when the program tries to write it, you'll get a
plain-English message telling you to close it and re-run. Nothing is lost:
the caches mean the re-run is fast and costs nothing.

## What happens when I re-run enrichment?

| Situation | What happens |
|---|---|
| **Same datasource file, run again** | Everything hits the cache — no API calls, no geocoding. The enriched file is rebuilt identically in seconds. Harmless. |
| **You replaced the file with a new version** | Rebuilt from the new file, paying only for what changed: new companies get geocoded and embedded, changed addresses get re-geocoded, changed descriptions get re-embedded, everything else comes from cache. **Companies no longer in the raw file disappear from the enriched file.** |
| **You added a new file with a different name** | Nothing — the program reads exactly the path in `config.yaml`, it does not scan the folder. Either rename your new file to `companies_raw.xlsx` or point `paths.companies_raw` at it. Stage 0 prints which file it read, so you can always confirm. |

The enriched file is always a full rebuild, never a merge — so it can never
drift from the raw datasource or keep stale rows for companies you removed.

Note: partnership and funding scores are cached by *company name*, so they
survive a datasource refresh even if a description changed. To force those
to be recomputed, delete `data/cache/company_scores.json`.

## Configuration

Everything tunable lives in `config.yaml`: file paths, score weights,
revenue thresholds, candidate-pool size, cutoffs, and model names. No code
edits needed to retune the system or move it to another machine.

## Tests

```bash
python run_tests.py          # 93 tests, ~5 seconds
python run_tests.py -v       # show each test name
```

Safe to run any time: every test builds its own throwaway project in a temp
folder and uses a stubbed AI client, so it never reads or writes your
`data/` folder and needs no API key or network connection. Run it after any
code change.

## Notes

- Interrupted run? Just re-run `match_faculty.py` — completed faculty are
  already flagged `Y` and are skipped automatically.
- Each output workbook has a **Run Info** sheet recording the research
  profile, weights, and settings used, so results are always traceable.
- Non-git users can get the code via GitHub's **Code → Download ZIP**,
  then follow the setup above.
