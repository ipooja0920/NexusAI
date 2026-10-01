#!/usr/bin/env python3
"""OpenAlex company-university research ties  ->  data/sources/openalex/ties/

For each US company in OpenAlex that could be one of our Capital IQ
companies (same normalised name, or same website), three questions:

  funded      papers the company FUNDED since 2005, and which universities
              and hospitals wrote them
  coauthored  papers the company's own staff WROTE since 2005, and which
              universities and hospitals they wrote them with
  by year     how many of each, per year since 2005

Each question is answered with OpenAlex "group_by" calls, which return
counts only - no paper titles and no author names are downloaded.

  per company, per role (funded / coauthored):
    1. universities, papers 2005 .. RECENT-1       (group_by institution)
    2. universities, papers RECENT .. today        (group_by institution)
    3. papers per year since 2005                  (group_by year)
  then one lookup per 100 co-author institutions: their type (education,
  healthcare, company, ...) and country, so universities can be picked out.

Which OpenAlex companies are asked about is decided generously here (any
name or website that COULD match); the real matching rule is applied at the
combine step, as for every other source.

Cost: OpenAlex needs a free API key (OPENALEX_API_KEY in .env) and gives
about $1 of calls a day; each call here costs $0.0001. Every answer is saved
as it arrives, so if the day's allowance runs out the run stops cleanly and
the next run picks up where it left off.

OpenAlex is not reachable from Claude's environments - run from Terminal.

Input:  data/sources/openalex/openalex_raw_institutions.json  (fetch_openalex.py)
        data/sources/openalex/openalex_raw_funders.json
        data/input/companies_raw.xlsx                        (Capital IQ)
Output: data/sources/openalex/ties/<openalex id>_<question>.json

Usage:
    python fetch_openalex_ties.py --test       # one company, prints the answers
    python fetch_openalex_ties.py              # everything (resumes)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC = PROJECT_ROOT / "data" / "sources" / "openalex"
OUT = SRC / "ties"
API = "https://api.openalex.org"
CONTACT = "lakshmipoojaraj@gmail.com"
SINCE = 2005
RECENT_FROM = 2024          # papers from this year on count as a CURRENT tie
PAUSE = 0.12
COST_PER_CALL = 0.0001


class OutOfBudget(Exception):
    pass


def short_id(url) -> str:
    return str(url or "").rsplit("/", 1)[-1]


def get(path: str, params: dict, key: str | None, retries: int = 4) -> dict:
    params = {**params, "mailto": CONTACT}
    if key:
        params["api_key"] = key
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": f"NexusAI-UConn ({CONTACT})", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            if e.code == 429:
                raise OutOfBudget(body) from e
            if e.code in (400, 401, 403):
                raise RuntimeError(f"OpenAlex rejected the request ({e.code}):\n{url}\n{body}") from e
            err = f"HTTP {e.code}"
        except Exception as e:                       # noqa: BLE001
            err = str(e)
        time.sleep(3 * attempt)
        print(f"    [retry {attempt}/{retries}] {err}")
    raise RuntimeError(f"giving up after {retries} attempts: {url}")


def group_by(filters: str, field: str, key) -> dict:
    """Every group for a filter, following the cursor. Returns
    {"count": total works, "groups": [{"key", "key_display_name", "count"}]}."""
    groups, cursor, total = [], "*", None
    while cursor:
        d = get("works", {"filter": filters, "group_by": field,
                          "per-page": 200, "cursor": cursor}, key)
        total = (d.get("meta") or {}).get("count") if total is None else total
        batch = d.get("group_by") or []
        groups.extend(batch)
        cursor = (d.get("meta") or {}).get("next_cursor")
        if not batch:
            break
        time.sleep(PAUSE)
    return {"count": total, "groups": groups}


def questions(org: dict, this_year: int) -> dict:
    """name -> works filter, for one organisation."""
    q = {}
    past, recent = f"{SINCE}-{RECENT_FROM - 1}", f"{RECENT_FROM}-{this_year}"
    if org.get("funder"):
        base = f"awards.funder_id:{org['funder']}"
        q["funded_past"] = (f"{base},publication_year:{past}", "authorships.institutions.id")
        q["funded_recent"] = (f"{base},publication_year:{recent}", "authorships.institutions.id")
        q["funded_by_year"] = (f"{base},publication_year:{SINCE}-{this_year}", "publication_year")
    if org.get("institution"):
        base = f"authorships.institutions.lineage:{org['institution']}"
        q["coauthored_past"] = (f"{base},publication_year:{past}", "authorships.institutions.id")
        q["coauthored_recent"] = (f"{base},publication_year:{recent}", "authorships.institutions.id")
        q["coauthored_by_year"] = (f"{base},publication_year:{SINCE}-{this_year}", "publication_year")
    return q


def organisations() -> list:
    """OpenAlex US companies, one entry per organisation: its institution
    id and/or funder id (linked through each record's roles), name(s),
    website, city, topics and paper count."""
    inst = json.loads((SRC / "openalex_raw_institutions.json").read_text(encoding="utf-8"))
    fund = json.loads((SRC / "openalex_raw_funders.json").read_text(encoding="utf-8"))
    funders = {short_id(f["id"]): f for f in fund}
    orgs, used_funders = [], set()
    for r in inst:
        fid = next((short_id(x["id"]) for x in r.get("roles") or [] if x.get("role") == "funder"), None)
        if fid:
            used_funders.add(fid)
        orgs.append({"institution": short_id(r["id"]), "funder": fid,
                     "name": r.get("display_name"),
                     "names": [r.get("display_name")] + (r.get("display_name_alternatives") or []),
                     "website": r.get("homepage_url") or (funders.get(fid) or {}).get("homepage_url"),
                     "city": (r.get("geo") or {}).get("city")})
    # funders that are companies but have no company institution record
    for fid, f in funders.items():
        if fid in used_funders:
            continue
        orgs.append({"institution": None, "funder": fid, "name": f.get("display_name"),
                     "names": [f.get("display_name")] + (f.get("alternate_titles") or []),
                     "website": f.get("homepage_url"), "city": None})
    return orgs


def candidates(orgs: list) -> list:
    """Organisations whose name or website COULD be a Capital IQ company."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from nexus.matching import normalise_company_name as nn, website_domain
    from nexus.settings import Settings
    from combine_companies import read_raw
    raw, name_col, site_col, _, _ = read_raw(Settings())
    names = {nn(n) for n in raw[name_col]} - {""}
    sites = {website_domain(w) for w in raw[site_col]} - {None}
    out = []
    for o in orgs:
        if any(nn(n) in names for n in o["names"] if n) or website_domain(o["website"]) in sites:
            out.append(o)
    return out


def lookup_institutions(ids: list, key, cache: Path) -> dict:
    """id -> {name, type, country}, 100 per call, cached."""
    known = json.loads(cache.read_text()) if cache.exists() else {}
    todo = [i for i in ids if i not in known]
    for k in range(0, len(todo), 100):
        chunk = todo[k:k + 100]
        d = get("institutions", {"filter": "openalex:" + "|".join(chunk),
                                 "select": "id,display_name,type,country_code",
                                 "per-page": 100}, key)
        for r in d.get("results") or []:
            known[short_id(r["id"])] = {"name": r.get("display_name"), "type": r.get("type"),
                                        "country": r.get("country_code")}
        for i in chunk:
            known.setdefault(i, {"name": None, "type": None, "country": None})
        cache.write_text(json.dumps(known))
        time.sleep(PAUSE)
    return known


def api_key() -> str | None:
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:                                # noqa: BLE001
        pass
    return (os.getenv("OPENALEX_API_KEY") or "").strip() or None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--test", action="store_true",
                    help="ask the questions for one well-known company and print the answers")
    args = ap.parse_args()
    key = api_key()
    this_year = date.today().year
    print("\n=== NexusAI: OpenAlex research ties ===\n")
    if not key:
        print("  No OPENALEX_API_KEY in .env - OpenAlex requires one (free at openalex.org).")
        return 1

    orgs = organisations()
    if args.test:
        o = next(x for x in orgs if x["funder"] and x["institution"]
                 and "pfizer" in (x["name"] or "").lower())
        print(f"  test company: {o['name']}  institution {o['institution']}  funder {o['funder']}")
        for q, (flt, field) in questions(o, this_year).items():
            r = group_by(flt, field, key)
            print(f"\n  {q}: {r['count']:,} papers, {len(r['groups'])} groups")
            for g in r["groups"][:5]:
                print(f"     {g}")
        ids = [short_id(g["key"]) for g in group_by(questions(o, this_year)["coauthored_recent"][0],
                                                     "authorships.institutions.id", key)["groups"][:5]]
        print("\n  institution lookup:", lookup_institutions(ids, key, Path("/tmp/oa_lookup_test.json")))
        print("\nIf these show counts and universities, run:  python fetch_openalex_ties.py\n")
        return 0

    todo = candidates(orgs)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "organisations.json").write_text(json.dumps(todo))
    calls = [(o, q, f, fld) for o in todo for q, (f, fld) in questions(o, this_year).items()]
    pending = [c for c in calls
               if not (OUT / f"{c[0]['institution'] or c[0]['funder']}_{c[1]}.json").exists()]
    print(f"  OpenAlex companies that could be ours: {len(todo):,}")
    print(f"  questions: {len(calls):,}  (already saved {len(calls) - len(pending):,})")
    print(f"  cost of what is left: about ${len(pending) * COST_PER_CALL:.2f} "
          "(free allowance is about $1 a day)\n")
    t0 = time.time()
    try:
        for n, (o, q, flt, fld) in enumerate(pending, 1):
            res = group_by(flt, fld, key)
            path = OUT / f"{o['institution'] or o['funder']}_{q}.json"
            path.write_text(json.dumps({"org": o, "question": q, "filter": flt, **res}))
            if n % 100 == 0 or n == len(pending):
                print(f"  {n:>6,}/{len(pending):,}   ({(time.time() - t0) / 60:.1f} min)")
            time.sleep(PAUSE)
        ids = sorted({short_id(g["key"])
                      for p in OUT.glob("*_*.json") if not p.name.startswith(("organisations", "institution"))
                      for g in json.loads(p.read_text())["groups"]
                      if "by_year" not in p.name})
        print(f"\n  looking up {len(ids):,} co-author institutions (type, country)...")
        lookup_institutions(ids, key, OUT / "institution_types.json")
    except OutOfBudget:
        print("\n  OpenAlex says today's free allowance is used up. Everything so far is")
        print("  saved - run the same command again tomorrow and it carries on.\n")
        return 2
    print(f"\n  done -> {OUT}")
    print("  Next: python clean_openalex.py\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
