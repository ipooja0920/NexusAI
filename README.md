# BridgeAI — Faculty ↔ Company Matching

BridgeAI matches UConn faculty to companies that are good candidates for a
research partnership. For one faculty member it reads their research profile
(registration form, scholarly publications and optional notes), compares it
with a Capital IQ company list enriched from public data, and writes one
Excel workbook with ranked, explained partner lists:

- **Small** companies (under $100m revenue, or revenue unknown) — top 50
- **Mid** companies ($100m – $1bn) — top 50
- **Large** companies ($1bn and above) — top 50
- **Hospitals & Clinics** — top 50 (any size)
- **Researcher's leads** — only when the faculty member named companies
- **All Companies** — every company, its scores and how far it got
- **Run Info** — exactly what was used for the run

Every listed company has a written justification that starts with a verdict
(Strong / Moderate / Weak fit, or Fit unclear).

How the matching and scoring work is described in
**[PRODUCT_SPEC.md](PRODUCT_SPEC.md)**; every output column is explained in
**[docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md)**;
**[COMMANDS.md](COMMANDS.md)** is a shorter command cheat-sheet.

> The product is called **BridgeAI**. The project folder (`NexusAI`), the
> GitHub repository and the Python package folder (`nexus/`) keep their
> original names, so existing paths and commands keep working.

---

## Quick start — one faculty member, start to finish

Run everything from the project folder, in Terminal:

```bash
cd ~/Downloads/NexusAI
```

1. **Company list.** Export the Capital IQ screen (see *The Capital IQ
   export* below), save it as a real `.xlsx` and put it at
   `data/input/companies_raw.xlsx`.
2. **Faculty files.**
   - Registration form export at
     `data/input/BDIFR Faculty Registration From-documents.csv` (exact name).
   - Their scholarly-contributions CSV (optional) in `data/input/publications/`.
   - Anything extra they told you (optional) in `data/input/faculty_notes.yaml`.
3. **Check the export:**
   ```bash
   python check_export.py
   ```
4. **OpenAlex research ties** for the companies in the export (needs
   `OPENALEX_API_KEY` in `.env`; already-fetched companies are skipped):
   ```bash
   python fetch_openalex_ties.py
   python clean_openalex.py
   ```
5. **Enrich the companies** (builds the combined file first, then enriches;
   runs in the background and keeps the Mac awake):
   ```bash
   ./run_enrich.sh
   ```
6. **Match the faculty member**, keeping the Mac awake while it runs:
   ```bash
   caffeinate -i python match_faculty.py --faculty "First Last"
   ```
   or by their row on the registration form: `--row 42`.
7. The workbook is written to `data/output/<Name> Match <date_time>.xlsx`.

Re-running the same faculty member, or another one on the same company list,
needs only step 6 — and is fast, because AI answers are cached.

---

## One-time setup

```bash
python3 -m venv venv                 # Windows: python -m venv venv
source venv/bin/activate             # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                 # then paste your keys into .env
```

`.env` holds `OPENAI_API_KEY` (required for matching and enrichment) and
`OPENALEX_API_KEY` (free, from <https://openalex.org/settings/api>; needed by
`fetch_openalex_ties.py`). It is git-ignored and never leaves your machine.

Requires Python 3.10+. Any Python environment with `requirements.txt`
installed works (a venv, or conda's `base`); `./run_enrich.sh` finds
`venv/` or `.venv/` by itself.

Check the install — no API key or network needed:

```bash
python run_tests.py                  # 546 tests
```

---

## Input files

| File | What it is | Notes |
|---|---|---|
| `data/input/companies_raw.xlsx` | Capital IQ screening export | Must be a real `.xlsx` (see below). Exact name. |
| `data/input/BDIFR Faculty Registration From-documents.csv` | BD-IFR faculty registration form export | **Exact name** — "From", not "Form". Only research fields are read. |
| `data/input/publications/*.csv` | Scholarly-contributions exports, one or more | Matched to the faculty member by the `Name` column; only Name, Title, Type and Year are read. |
| `data/input/faculty_notes.yaml` | Optional per-faculty adjustments | See *Faculty notes* below. |
| `data/input/Faculty Database.xlsx` | Older faculty list with a `Flag` column | Run with no `--faculty` / `--row` to match everyone with `Flag = N`; `--faculty` also looks here if the name is not on the registration form. |

**Registration form downloads get renamed.** Google Sheets often saves it
as `BDIFR Faculty Registration From-documents - BDIFR Faculty Registration
From-documents.csv.csv`. Rename it to the exact name above and remove or
rename the older copy. If the file is missing, `match_faculty.py --faculty`
reports "not found" and lists only the Faculty Database names.

**Publications files** must sit in `data/input/publications/` — a CSV left
directly in `data/input/` is not read. The file name does not matter; the
`Name` column does. Names are matched ignoring accents, case and
punctuation, and then by first + last name, so "Mary Amalaradjou" matches
"Mary Anne Roshni Amalaradjou" and "Md Uddin" matches "Md Elias Uddin".
Check the titles: these exports can contain another researcher's papers
under the same name (use `ignore_publications` in the faculty notes).

---

## The Capital IQ export

**Screen criteria** used so far:

- **Industry Classifications** — the faculty member's sector(s), e.g.
  *Pharmaceuticals, Biotechnology and Life Sciences*. Pick every industry
  their work touches; companies outside the screen can never be matched
  (an ocean-waves researcher needs more than *Software & Services*).
- **Geographic Locations** — *Northeast* and *Midatlantic*. Capital IQ does
  not put **Pennsylvania** in either: add it as its own location (search
  "Pennsylvania" in the location picker), or Philadelphia/Pittsburgh
  companies are missing.
- **Company Type** — Public Company, Private Company.
- **Company Status** — Operating, Operating Subsidiary (Reorganizing is
  also kept). Acquired, Out of Business and Liquidating companies are left
  out of every tab anyway.

**Columns (data items) to export** — any order; they are found by header
name on row 8:

| Required | Optional |
|---|---|
| Company Name, Industry Classifications, Company Type, Total Employees [Latest Annual], Total Revenue [LTM] ($USDmm, Historical rate), Offices, Business Description, Website, Company Status | Exchange:Ticker |

Other columns (Geographic Locations, Competitors, ...) are allowed and
ignored.

**Save as `.xlsx`.** Capital IQ downloads `.xls`; renaming it is not enough
(it fails with `BadZipFile`). Open it in Excel and *Save As → Excel Workbook
(.xlsx)*.

**Two exports for one list** (e.g. Northeast + Midatlantic, and Pennsylvania
separately) must be stitched into one sheet with one header row before
`check_export.py`. A company in both should appear once.

Then:

```bash
python check_export.py               # every needed column found and filled in?
```

---

## Running each step

### Enrichment — `./run_enrich.sh`

Runs `combine_companies.py` (Capital IQ + SBIR, Form D, SEC XBRL,
ClinicalTrials.gov, OpenAlex → `data/companies_combined.xlsx`) and then
`enrich_companies.py` (geocoding, distance, size and funding scores, AI
company profiles, embeddings → `data/companies_enriched.xlsx`). The job runs
in the background under `caffeinate`, so it carries on if you close Terminal
or the screen sleeps, and it shows progress live with a time estimate.

```bash
./run_enrich.sh              # start and watch
./run_enrich.sh --no-watch   # start and get the prompt back
./run_enrich.sh --watch      # watch a running job again
./run_enrich.sh --status     # one line: running or not, last progress
./run_enrich.sh --stop       # stop the job
```

- **Ctrl-C while watching stops only the watching** — the job keeps going.
  (The "Terminated: 15 tail -n 20 -f" line at the end is that watcher
  closing, not an error.)
- It refuses to start a second run while one is going.
- **Keep the lid open.** Closing it sleeps the Mac and pauses the job. With
  power and an external display, clamshell mode works.
- Re-running is cheap for companies already done: geocodes, AI profiles and
  embeddings are cached; only new or changed companies cost time.

Foreground alternatives (testing): `python combine_companies.py` then
`python enrich_companies.py` with `--skip-geocode`, `--skip-embeddings` or
`--skip-profiles`.

### Matching — `match_faculty.py`

```bash
caffeinate -i python match_faculty.py --faculty "Sharon Casavant"
caffeinate -i python match_faculty.py --row 19          # row on the registration form
python match_faculty.py --row 19 --dry-run              # what it would do, no API spend
python match_faculty.py --row 19 --sample 200           # small cost / speed test
caffeinate -i python match_faculty.py --row 19 --full   # AI-score EVERY small company (hours)
python match_faculty.py                                 # Faculty Database: everyone with Flag = N
```

- **`caffeinate -i`** keeps the Mac from idle-sleeping until the run ends
  (the lid must still stay open).
- **To stop a run, press Ctrl-C.** AI answers so far are cached; running the
  same command again resumes from there.
- **Do not press Ctrl-Z** — it *pauses* the run instead of stopping it. If
  you did, type `jobs` and then `kill %1` (the number `jobs` shows).
- Lines like `python(1234) MallocStackLogging: can't turn off malloc stack
  logging ...` are a harmless macOS message.
- The first run for a faculty member on a large list makes many AI calls
  (three per company scored), so it can take a while; named leads add more.
  If OpenAI reports rate-limit errors, lower `openai.concurrency` in
  `config.yaml`.

### Faculty notes — `data/input/faculty_notes.yaml` (optional)

Adjusts **one** faculty member's run. Anyone not listed runs exactly as
usual. Names are matched like `--faculty`. Every field is optional:

```yaml
"Sharon Casavant":
  extra_research: |
    A refined research description the faculty member sent.
  distance_full_score_miles: 400      # full distance score up to 400 miles
  leads:                              # companies they named
    - name: Jazz Pharmaceuticals
      note: What they told you about it (interest, grants, caveats).
    - GW Pharmaceuticals              # a name alone is fine
  ignore_publications:                # leave out titles containing these words
    - HIV stigma
```

- **extra_research** — added to the research profile, and also searched on
  its own when the shortlist is built (Found By "researcher's notes").
- **distance_full_score_miles** — companies up to this distance get the full
  distance score; beyond it the usual decline starts from there.
- **leads** — the companies *named* like each lead (or, if none is, those
  naming it as their parent) are always AI-scored, shown on a
  **Researcher's leads** sheet (first sheet), and not penalised for
  distance. The note is read by the AI with the company's profile. Companies
  the lead acquired are not treated as the lead. A lead missing from the
  export is taken from the lists under `faculty_notes: lead_sources:` in
  `config.yaml`. Each lead's description is also searched for similar
  companies ("like lead: …" in Found By) — with many leads this adds a lot of
  scoring time.
- **ignore_publications** — any title containing one of these words is left
  out.
- Run Info gets a **Faculty notes used** line. A misspelt field name stops
  the run with a message naming it.

### Keeping several company lists

Only one list is active at a time (`companies_raw.xlsx` → enriched). To keep
a list for later, copy its raw export (and, optionally, its
`companies_enriched.xlsx`) into a folder under `data/datasets/<name>/`. To
switch back, copy the raw export to `data/input/companies_raw.xlsx` and run
steps 3–5 of the quick start; cached work makes this much faster than the
first time. Make sure `companies_enriched.xlsx` is the right list before
matching — Run Info shows the number of companies used.

---

## Reading the output

- **Verdict** (start of each justification) — research fit only: Strong
  (alignment 7–9), Moderate (5–6), Weak (1–4), Fit unclear (no score).
- **Overall Score** — research fit (30%) plus distance (25%), partnership
  likelihood (15%), revenue (15%), funding available (10%) and employees
  (5%). Tabs are sorted by it, so a nearby, well-funded Weak fit can rank
  above a distant Strong fit (`funnel.tab_order: verdict_first` lists Strong
  first instead).
- Mid and Large list only companies with alignment 4 or more; hospitals and
  clinics also need 4 or more.
- One company per corporate group is listed; the others show "Same group as
  … (listed)" in All Companies.
- A justification ending "Note: Capital IQ has no website for this company"
  flags a record that may be out of date — check it still operates.
- **All Companies → Stage / Found By** tell you how far each company got and
  which search brought it in.

Column-by-column definitions: [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md).

---

## The `data/` folder

`data/` is git-ignored — licensed Capital IQ data, faculty files and
results never go to GitHub.

```
data/
├── input/                      files YOU provide (never written by the programs)
│   ├── companies_raw.xlsx
│   ├── BDIFR Faculty Registration From-documents.csv
│   ├── publications/            scholarly-contributions CSVs
│   ├── faculty_notes.yaml       optional
│   └── Faculty Database.xlsx    older faculty list (Flag column)
├── sources/                    downloaded public data (re-downloadable)
│   ├── formd/  sbir/  xbrl/  openalex/  ...
├── cache/                      computed by us (delete only to force a rebuild)
│   ├── geocode.json, embeddings.json, company_profiles.json
│   ├── alignment_scores.json, company_scores.json   AI answers
│   └── *_clean.csv, openalex_*.csv                  cleaned source data
├── datasets/                   saved company lists (also the lead sources)
├── review/                     match-quality sheets, to check by eye
├── output/                     one workbook per faculty run
├── companies_combined.xlsx     Capital IQ + source facts  (combine_companies.py)
└── companies_enriched.xlsx     + scores and profiles      (enrich_companies.py)
```

| You manage | The programs manage |
|---|---|
| everything in `input/`, the downloads in `sources/formd/` and `sources/sbir/`, saved lists in `datasets/` | `cache/`, `review/`, `output/`, the combined and enriched workbooks |

If a workbook is open in Excel when a program writes it, you get a message
to close it and re-run; nothing is lost.

---

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
anyway. Unlike Form D, SBIR is a
single CSV covering every award since 1983, so history is free here. Filtering
at download time just means re-downloading when you change your mind.

There is also an Award API at <https://www.sbir.gov/api>. Don't use it: it was
under maintenance as of September 2026, it exposes fewer fields than the bulk
file, and 200k awards over the network is slower than one file you re-read for
free.

SBIR refreshes the file monthly.

### SEC XBRL — R&D, revenue and cash for public companies

**No manual download** — `fetch_sec_xbrl.py` pulls it from SEC's API, and
`clean_xbrl.py` turns it into `data/cache/xbrl_clean.csv`:

```bash
python fetch_sec_xbrl.py                    # CY2023-CY2025 + the last 4 quarter-ends
python fetch_sec_xbrl.py --years 2024 2025
python fetch_sec_xbrl.py --quarters 2026Q1 2026Q2
python fetch_sec_xbrl.py --refresh          # ignore the cache and refetch
python clean_xbrl.py
```

It fetches R&D expense and revenue (money over a year) and cash and
short-term investments (money held at a quarter-end). Capital IQ says how big
a company is, not how much it spends on research or has in the bank; for a
partnership those are the more useful numbers. Revenue is read from several
XBRL tags, because companies split between the older "Revenues" tag and the
newer contract-revenue tags.

It downloads from two places:

| What | Where |
|---|---|
| One concept, one period, every filer | `https://data.sec.gov/api/xbrl/frames/us-gaap/<Tag>/USD/CY2024.json` |
| Ticker → CIK map | <https://www.sec.gov/files/company_tickers.json> |

SEC requires a `User-Agent` header and returns a bare `403` without one; the
script sets it. XBRL only reaches publicly listed companies — a private
company files none — so it improves what you know about companies you
already have rather than reaching new ones.

### OpenAlex — research and funding track record

No manual download — `fetch_openalex.py` pulls it from the API into
`data/sources/openalex/`, and `fetch_openalex_ties.py` asks, for each company
that could be one of yours, which universities it funded or co-wrote papers
with (counts only — no titles or author names). `clean_openalex.py` turns
that into `data/cache/openalex_companies.csv` and `openalex_ties.csv`. An API key is optional but raises the daily budget
from $0.10 to $1; get one free at <https://openalex.org/settings/api> and add
it to `.env` as `OPENALEX_API_KEY=...`.

### Refreshing a source

```bash
python clean_sbir.py              # or clean_formd.py / clean_xbrl.py /
                                  # clean_clinicaltrials.py / clean_openalex.py
./run_enrich.sh                   # rebuilds the combined file, then enriches
```

ClinicalTrials.gov downloads itself (run from your own Terminal):

```bash
python fetch_clinicaltrials.py --test     # 3 trials, check it works
python fetch_clinicaltrials.py
python clean_clinicaltrials.py
```

Order does not matter: a clean file that does not exist yet leaves its
columns blank. (`enrich_funding.py`, `enrich_sbir.py` and `enrich_xbrl.py`
are the older path and no longer feed the enriched file.)

To check a Form D figure by eye:

```bash
python export_formd_raw.py --company Agilix   # → data/review/formd_raw.csv
python inspect_formd.py "Agilix"              # straight to the screen
```

---

## Configuration

Everything tunable is in `config.yaml`: paths, Capital IQ header row, AI
models and concurrency, alignment votes, shortlist sizes, size tiers, tab
size and order, minimum scores, score weights and component formulas,
funding and university-track-record settings, faculty-notes lead sources.
No code changes are needed to retune a run.

## Tests

```bash
python run_tests.py          # 546 tests, stubbed AI, no network
python run_tests.py -v       # each test name
```

Every test builds its own throwaway project, so it never touches `data/`.
Run it after any code change.

## Saving changes to GitHub

```bash
cd ~/Downloads/NexusAI
git status
git push origin main         # no dot at the end
```

`git push` succeeded when the last line reads like `abc123..def456  main -> main`.
If git reports a `.lock` file "File exists", no other git command is
running, and you did not create it, the lock is left over and can be removed.

## Troubleshooting

| Message | Cause and fix |
|---|---|
| `Faculty '…' not found in the database. Names available: Ioulia Valla` | The registration CSV is missing or misnamed — see *Input files*. |
| `'…' matches more than one faculty … use --row N` | Two people share a first + last name; use `--row`. |
| `Publications used: none found` in Run Info | No CSV in `data/input/publications/` has a matching `Name`. |
| `BadZipFile: File is not a zip file` | The export is an `.xls` renamed to `.xlsx` — re-save it as a real `.xlsx`. |
| `check_export.py` reports a missing or empty column | Add that data item to the Capital IQ screen and export again. |
| Results show the wrong number of companies | `companies_enriched.xlsx` is another list — re-run `./run_enrich.sh` for the right export. |
