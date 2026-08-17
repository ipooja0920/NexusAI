# NexusAI — Faculty ↔ Company Matching

Matches UConn faculty research profiles against a Capital IQ company
datasource and produces a ranked top-20 workbook per faculty member.

See **[PRODUCT_SPEC.md](PRODUCT_SPEC.md)** for the full architecture, how
it differs from the previous watcher/monster-program system, and the
scoring model reference.

## How it works

**Stage 0 — `enrich_companies.py`** (run once, and again only when you
refresh the Capital IQ extract): cleans every company record, geocodes the
address → distance from UConn Storrs, parses revenue/employees into
component scores, and computes a description embedding. Results are written
to `data/companies_enriched.xlsx`; geocodes and embeddings are cached in
`data/cache/` so a datasource refresh only pays for what changed.

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

## Data files you provide (not in the repo)

`data/` is git-ignored — Capital IQ extracts and faculty lists never go to
GitHub. On a fresh clone, create the `data/` folder and add:

| File | What it is |
|---|---|
| `data/companies_raw.xlsx` | Capital IQ screening export (headers on row 8, data from row 9; columns A=name, C=employees, D=revenue $USDmm, G=address, I=description, J=website) |
| `data/Faculty Database.xlsx` | Headers on row 1: Faculty, School/College, Department, Research Description, Industry Classification1, Industry Classification2, Flag (N = not yet matched) |

## Running

```bash
source venv/bin/activate            # (each new terminal session)

python enrich_companies.py          # after adding/refreshing the datasource
python match_faculty.py             # match everyone with Flag = N
```

Useful options:

```bash
python match_faculty.py --dry-run             # show what would run, no API spend
python match_faculty.py --faculty "Ioulia Valla"   # force one faculty, ignore flag
python enrich_companies.py --skip-geocode     # skip geocoding (testing only)
python test_smoke.py                          # offline end-to-end test, no API key needed
```

## Configuration

Everything tunable lives in `config.yaml`: file paths, score weights,
revenue thresholds, candidate-pool size, cutoffs, and model names. No code
edits needed to retune the system or move it to another machine.

## Notes

- Interrupted run? Just re-run `match_faculty.py` — completed faculty are
  already flagged `Y` and are skipped automatically.
- Each output workbook has a **Run Info** sheet recording the research
  profile, weights, and settings used, so results are always traceable.
- Non-git users can get the code via GitHub's **Code → Download ZIP**,
  then follow the setup above.
