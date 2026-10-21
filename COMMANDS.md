# NexusAI — Command Guide

Everything below runs from the project folder:

```bash
cd ~/Downloads/NexusAI
```

---

## 0. First time only

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# open .env and paste your OpenAI key
```

`.env` is git-ignored and never leaves your machine.

Check it worked:

```bash
python run_tests.py          # 404 tests, ~10 seconds, no API key or network needed
```

After this you do **not** need `source venv/bin/activate` for `./run_enrich.sh` —
it finds the venv itself. You do still need it for the `python …` commands.

---

## 1. Put your files in place

| File | Where | Who provides it |
|---|---|---|
| Company export | `data/input/companies_raw.xlsx` | You, from Capital IQ |
| Faculty list | `data/input/Faculty Database.xlsx` | You |

**The company export must be a real `.xlsx`.** Capital IQ hands you an `.xls`.
Renaming it is not enough — the pipeline uses openpyxl, which only reads true
`.xlsx`, and a renamed file fails with `BadZipFile: File is not a zip file`.
Open it in Excel and *Save As → Excel Workbook (.xlsx)*.

**If the column order changed**, update `config.yaml` to match. The config
addresses fields by letter, so a re-run of the screening report that returns
the same fields in a different order will silently attach data to the wrong
field. Check the header row (row 8) against:

```yaml
capitaliq:
  col_company: A
  col_ticker: B
  col_industry: C
  col_company_type: D
  col_employees: F
  col_revenue: G
  col_address: I
  col_description: K
  col_website: L
```

---

## 2. Funding sources — optional, but do them first

Each public dataset has its own script. They read a download, match it to your
companies, and write to `data/cache/`. Skip any of them and its columns come out
blank — nothing breaks.

Two verbs: **`fetch_*` downloads, `enrich_*` matches.**

### SEC Form D — private capital raises

Download quarterly ZIPs from
<https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets>
(*Data & Research → SEC & Markets Data*; updated every quarter).
Drop them in `data/sources/formd/` — zipped or unzipped, both work.

```bash
source venv/bin/activate
python enrich_funding.py
python enrich_funding.py --min-year 2023      # ignore older filings
```

### SBIR / STTR — federal R&D awards

Download from <https://www.sbir.gov/awards> (year filter available).
Save as `data/sources/sbir/award_data.csv`.

```bash
python enrich_sbir.py
python enrich_sbir.py --recent-years 3        # narrow the "recent funding" window
```

### SEC XBRL — R&D, revenue, cash and short-term investments (public companies)

This one downloads for itself, so it needs both steps:

```bash
python fetch_sec_xbrl.py                      # → data/sources/xbrl/
python fetch_sec_xbrl.py --years 2024 2025
python fetch_sec_xbrl.py --quarters 2026Q1 2026Q2
python fetch_sec_xbrl.py --refresh            # ignore cache, refetch

python clean_xbrl.py                          # → data/cache/xbrl_clean.csv
python enrich_xbrl.py                         # older path → data/cache/rd_xbrl.json
```

### Combined file

After any of the clean steps:

```bash
python clean_sbir.py
python clean_formd.py
python clean_xbrl.py
python combine_companies.py                   # → data/companies_combined.xlsx
```

### ClinicalTrials.gov — company trials, university partners, Connecticut sites

Downloads for itself (from your Terminal - it is blocked for Claude):

```bash
python fetch_clinicaltrials.py --test         # 3 trials, check it works
python fetch_clinicaltrials.py                # everything, ~5-10 minutes
python clean_clinicaltrials.py                # → data/cache/ctgov_clean.csv
```

### OpenAlex — research and funding track record

```bash
python fetch_openalex.py                      # → data/sources/openalex/
python fetch_openalex.py --refresh
```

Order among these does not matter.

---

## 3. Enrich the companies — Stage 0

First rebuilds `data/companies_combined.xlsx` (Capital IQ + every clean
source file, a couple of minutes), then geocodes every address, computes
distance from Storrs and all the data scores (revenue, funding, academic
track record), writes an AI company profile for the ~2,000 companies with
SBIR / trial / OpenAlex text (Capital IQ description + that text), and embeds
every profile. This is the long one - but geocodes, profiles and embeddings
are cached.

```bash
./run_enrich.sh
```

Prints a PID and a log path, then hands you back the prompt:

```
  started    PID 4821
  log        logs/enrich_20260916_215746.log

  watch it   tail -f logs/enrich_20260916_215746.log
  check it   kill -0 4821 && echo running || echo finished
  stop it    kill 4821

  Safe to close this window. Keep the lid open.
```

You can close Terminal, quit Terminal, or log out. **Keep the lid open** —
closing it sleeps the Mac and pauses the job. For a closed lid, plug into power
with an external display (clamshell mode); the same command works.

It refuses to start if a run is already going, so you can't have two fighting
over the same caches.

Writes `data/companies_enriched.xlsx`.

Foreground instead, if you want to watch it:

```bash
source venv/bin/activate
python enrich_companies.py
python enrich_companies.py --skip-geocode     # testing only
python enrich_companies.py --skip-embeddings  # testing only
python enrich_companies.py --skip-profiles    # plain descriptions, no AI profiles
```

---

## 4. Match faculty — Stage 1

```bash
source venv/bin/activate

# from the BD-IFR registration form (data/input/BDIFR Faculty Registration From-documents.csv)
python match_faculty.py --row 13 --dry-run          # what it would do, no API spend
python match_faculty.py --row 13 --sample 200       # test: prints time and tokens per call
python match_faculty.py --row 13                    # Small (pre-filter), Mid, Large, Hospitals & Clinics + All Companies
python match_faculty.py --row 13 --full             # Small tab from ALL small companies (~18,000 AI calls, hours)
python match_faculty.py --faculty "Julie Burland"   # same, by name

# old Faculty Database
python match_faculty.py                             # everyone with Flag = N
python match_faculty.py --faculty "Ioulia Valla"    # one person, ignores the flag
```

Writes one workbook per faculty to `data/output/`, ranked separately for
Small (under $100m or revenue unknown), Mid ($100m-$1bn) and Large ($1bn+)
companies - thresholds in `size_tiers` in config.yaml. The Small - all
companies pass makes ~20,000 AI calls, `openai.concurrency` (16) at a time. If OpenAI
reports rate-limit errors, lower `concurrency` in config.yaml. Scores are
cached, so Ctrl-C and re-run picks up where it stopped.

---

## 5. Checking a number

When a figure in the enriched workbook looks wrong, these print the filings
underneath it — SEC's own column names, no cleaning applied:

```bash
python export_formd_raw.py                    # → data/review/formd_raw.csv
python export_formd_raw.py --company Agilix
python inspect_formd.py "Agilix"              # straight to the screen
```

Two helper columns say what the pipeline *would* do without doing it:
`_would_be_dropped` names the reason a row would be discarded, `_span_years`
is the gap between first sale and filing date.

The `data/review/` workbooks written by each source step show its matches for
eye-checking.

---

## Day to day

Once everything is set up, this is the whole loop:

```bash
python match_faculty.py
```

`./run_enrich.sh` is only needed when the company export changes, or a source
step has refreshed a cache.

### After a new Capital IQ export

```bash
# 1. save as real .xlsx, put in data/input/companies_raw.xlsx
# 2. check the columns against config.yaml (letters, and that they are filled):
python check_export.py
python run_tests.py
./run_enrich.sh
```

Re-running is cheap for companies you already had — geocodes and embeddings are
cached, so you only pay for the new ones.

### After a new funding download

```bash
source venv/bin/activate
python clean_sbir.py         # or clean_formd.py / clean_xbrl.py /
                             # clean_clinicaltrials.py / clean_openalex.py
./run_enrich.sh              # rebuilds the combined file, then enriches
```

(The older `enrich_sbir.py`, `enrich_funding.py` and `enrich_xbrl.py` no
longer feed the enriched file - the clean files + combine step replaced them.)

---

## Which folders hold what

```
data/input/       files YOU provide
data/sources/     downloaded from outside   — re-download to replace
data/cache/       computed by us            — re-compute to replace
data/review/      match-quality sheets, for checking by eye
data/output/      final per-faculty results
logs/             written by run_enrich.sh
```

`data/` and `.env` are git-ignored — your licensed Capital IQ export and the
faculty list never reach GitHub.

---

## Pushing to GitHub

```bash
git status
git add -A
git commit -m "your message"
git push origin main
```


## Faculty publications (optional)

Put a faculty member's scholarly contributions export (CSV with Name, Title,
Type, Year) in `data/input/publications/`. The next match run for that
faculty picks it up by name - nothing else to run. Run Info shows the titles
used. Weights are under `publications:` in config.yaml.
