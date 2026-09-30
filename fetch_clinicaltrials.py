#!/usr/bin/env python3
"""Download company trials from ClinicalTrials.gov  ->  data/sources/clinicaltrials/

Every trial where a COMPANY is involved - as the lead sponsor, or as a
collaborator on someone else's trial (a university-led trial a company
helps pay for is exactly the kind of tie we want to see). Trials run only
by universities, hospitals or government are not downloaded.

Only the fields NexusAI uses are requested, and nothing that names a person:
investigators, contacts and officials are never downloaded at all.

  who     lead sponsor and collaborators, each with ClinicalTrials.gov's class
          (INDUSTRY = company, OTHER = university / hospital / foundation,
          NIH, FED, ...)
  when    overall status, start / completion / last-update dates
  what    phase, study type, enrolment, conditions, treatments
  where   site facility, city, state, ZIP, country, site status

The API is ClinicalTrials.gov's public v2 API - no key needed.
ClinicalTrials.gov is not reachable from Claude's environments, so this
runs from your own Terminal.

Output: data/sources/clinicaltrials/page_00001.json.gz, page_00002 ...
        (gzip-compressed API pages, read by clean_clinicaltrials.py)
        The old pages are replaced only once the new download has finished,
        so a failed run never leaves you with half a download.

Usage:
    python fetch_clinicaltrials.py --test     # 3 trials, prints what came back
    python fetch_clinicaltrials.py            # everything, ~5-10 minutes
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
OUT_DIR = PROJECT_ROOT / "data" / "sources" / "clinicaltrials"
API = "https://clinicaltrials.gov/api/v2/studies"

# a company is the lead sponsor, or one of the collaborators
COMPANY_TRIALS = "AREA[LeadSponsorClass]INDUSTRY OR AREA[CollaboratorClass]INDUSTRY"

# Named fields only - never a whole module, because the contacts module
# also carries people's names, phone numbers and e-mail addresses.
FIELDS = [
    "NCTId", "BriefTitle",
    "OverallStatus", "StartDate", "PrimaryCompletionDate", "CompletionDate",
    "LastUpdatePostDate",
    "LeadSponsorName", "LeadSponsorClass", "CollaboratorName", "CollaboratorClass",
    "StudyType", "Phase", "EnrollmentCount",
    "Condition", "InterventionType", "InterventionName",
    "LocationFacility", "LocationCity", "LocationState", "LocationZip",
    "LocationCountry", "LocationStatus",
]

PAGE_SIZE = 1000          # the API's maximum
PAUSE = 1.5               # seconds between calls - ClinicalTrials.gov asks
                          # for roughly 50 requests a minute at most
HEADERS = {"User-Agent": "NexusAI-UConn research matching (lakshmipoojaraj@gmail.com)",
           "Accept": "application/json"}


def get_page(token: str | None, size: int, count_total: bool, retries: int = 5) -> dict:
    params = {"format": "json", "pageSize": size, "filter.advanced": COMPANY_TRIALS,
              "fields": ",".join(FIELDS)}
    if count_total:
        params["countTotal"] = "true"
    if token:
        params["pageToken"] = token
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500]
            if e.code == 400:
                # a wrong field name or filter - retrying will not help
                raise RuntimeError(f"ClinicalTrials.gov rejected the request (400):\n{body}") from e
            err = f"HTTP {e.code}"
        except Exception as e:                      # noqa: BLE001
            err = str(e)
        wait = 5 * attempt
        print(f"    [retry {attempt}/{retries}] {err} - waiting {wait}s")
        time.sleep(wait)
    raise RuntimeError(f"giving up after {retries} attempts")


def test() -> int:
    print("\nAsking ClinicalTrials.gov for 3 company trials...\n")
    page = get_page(None, 3, True)
    print(f"  company trials in total: {page.get('totalCount', '?'):,}"
          if isinstance(page.get("totalCount"), int) else "  (no total returned)")
    for s in page.get("studies", []):
        print(json.dumps(s, indent=1)[:1500])
        print("  ...")
    print("\nIf this shows trials with sponsors, conditions and locations, run:")
    print("  python fetch_clinicaltrials.py\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--test", action="store_true",
                    help="fetch 3 trials and print them - nothing saved")
    args = ap.parse_args()
    if args.test:
        return test()

    partial = OUT_DIR.with_name("clinicaltrials.partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)

    print("\n=== NexusAI: ClinicalTrials.gov download ===\n")
    token, n, studies, total = None, 0, 0, None
    started = time.time()
    while True:
        page = get_page(token, PAGE_SIZE, count_total=(n == 0))
        if n == 0:
            total = page.get("totalCount")
            print(f"  company trials to download: {total:,}" if total else "")
        n += 1
        studies += len(page.get("studies", []))
        with gzip.open(partial / f"page_{n:05d}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(page, fh)
        mins = (time.time() - started) / 60
        print(f"  page {n:>4}  {studies:>8,} trials" +
              (f" of {total:,}" if total else "") + f"   ({mins:.1f} min)")
        token = page.get("nextPageToken")
        if not token:
            break
        time.sleep(PAUSE)

    shutil.rmtree(OUT_DIR, ignore_errors=True)
    partial.rename(OUT_DIR)
    print(f"\n  {studies:,} trials in {n} pages -> {OUT_DIR}")
    print("  Next: python clean_clinicaltrials.py\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
