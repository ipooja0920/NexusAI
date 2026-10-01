"""Tests for the OpenAlex columns in combine_companies.py. Made-up rows."""
from __future__ import annotations

import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

import pandas as pd

from combine_companies import _add_years, match_company, summarise_openalex

COLS = ["institution_id", "funder_id", "company", "website", "city", "topics",
        "papers_since_2005", "papers_by_year", "funded_papers_since_2005",
        "funded_papers_by_year", "funded_universities", "coauthor_universities"]


def summary(*rows):
    f = Path(tempfile.mkdtemp()) / "oa.csv"
    pd.DataFrame([{**{c: None for c in COLS}, **r} for r in rows]).to_csv(f, index=False)
    return summarise_openalex(f)


def match(name, website, s):
    by_name, by_dom = defaultdict(list), defaultdict(set)
    for k, v in s.items():
        by_name[v["norm"]].append(k)
        for d in v["domains"]:
            by_dom[d].add(k)
    by_dom = {d: next(iter(k)) for d, k in by_dom.items() if len(k) == 1}
    return match_company(name, website, None, s, by_name, by_dom)[:2]


class TestOpenAlex(unittest.TestCase):

    def test_country_tag_ignored_but_legal_form_needs_the_website(self):
        s = summary(dict(institution_id="I1", company="Abiomed (United States)", website="https://abiomed.com"))
        self.assertEqual(match("Abiomed", "www.abiomed.com", s)[1], "name")
        self.assertEqual(match("Abiomed, Inc.", "www.abiomed.com", s)[1], "name+website")
        self.assertEqual(match("Abiomed, Inc.", "www.other.com", s), (None, "unconfirmed"))

    def test_website_only_match(self):
        s = summary(dict(institution_id="I1", company="Celldex Therapeutics (United States)",
                         website="http://www.celldex.com"))
        self.assertEqual(match("Celldex Research Corporation", "celldex.com", s)[1], "website")

    def test_indicators(self):
        s = summary(dict(institution_id="I1", funder_id="F1", company="Acme (United States)",
                         funded_universities="Yale University (3)", coauthor_universities=None,
                         funded_papers_by_year="2025:2; 2024:1", topics="Gene therapy"))
        v = next(iter(s.values()))
        self.assertEqual((v["oa_funded_university"], v["oa_coauthored_university"]), (True, False))
        self.assertEqual((v["oa_funded_papers_by_year"], v["oa_topics"]), ("2025:2; 2024:1", "Gene therapy"))

    def test_funder_and_institution_records_of_one_company_merged(self):
        """'Acorda Therapeutics' (funder) and 'Acorda Therapeutics (United States)'
        (institution) are one company, not two namesakes."""
        s = summary(dict(funder_id="F1", company="Acorda Therapeutics", funded_papers_by_year="2024:1",
                         funded_universities="Yale University (1)"),
                    dict(institution_id="I1", company="Acorda Therapeutics (United States)",
                         funded_papers_by_year="2024:2; 2020:1", coauthor_universities="MIT (4)",
                         topics="Multiple sclerosis", website="acorda.com"))
        self.assertEqual(len(s), 1)
        v = next(iter(s.values()))
        self.assertEqual((v["oa_funded_university"], v["oa_coauthored_university"]), (True, True))
        self.assertEqual(v["oa_funded_papers_by_year"], "2024:3; 2020:1")
        self.assertEqual(v["oa_topics"], "Multiple sclerosis")

    def test_add_years(self):
        self.assertEqual(_add_years(["2025:1; 2024:2", None, "2024:1"]), "2025:1; 2024:3")
        self.assertIsNone(_add_years([None, float("nan")]))


if __name__ == "__main__":
    unittest.main()
