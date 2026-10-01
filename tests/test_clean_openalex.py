"""Tests for clean_openalex.py - saved OpenAlex answers to the two clean
files. Made-up answers in a temp folder; no network."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clean_openalex import by_year, clean


def groups(*pairs):
    return [{"key": f"https://openalex.org/{k}", "key_display_name": k, "count": n} for k, n in pairs]


def setup(org, answers, types, raw=()):
    d = Path(tempfile.mkdtemp())
    (d / "organisations.json").write_text(json.dumps([org]))
    (d / "institution_types.json").write_text(json.dumps(types))
    key = org["institution"] or org["funder"]
    for q, (count, grp) in answers.items():
        (d / f"{key}_{q}.json").write_text(json.dumps({"count": count, "groups": grp, "question": q}))
    rawf = d / "raw.json"
    rawf.write_text(json.dumps(list(raw)))
    return clean(d, rawf)


ORG = {"institution": "I1", "funder": "F1", "name": "Acme Bio (United States)",
       "website": "https://acme.com", "city": "Boston"}
TYPES = {"U1": {"name": "Yale University", "type": "education", "country": "US"},
         "H1": {"name": "Hartford Hospital", "type": "healthcare", "country": "US"},
         "C1": {"name": "Other Pharma", "type": "company", "country": "US"},
         "U2": {"name": "University of Oxford", "type": "education", "country": "GB"}}


class TestClean(unittest.TestCase):

    def test_universities_and_hospitals_only(self):
        _, t = setup(ORG, {"coauthored_past": (10, groups(("U1", 4), ("H1", 2), ("C1", 9))),
                           "coauthored_recent": (3, groups(("U1", 1)))}, TYPES)
        self.assertEqual(sorted(t.university), ["Hartford Hospital", "Yale University"])

    def test_past_and_recent_added_per_university(self):
        _, t = setup(ORG, {"funded_past": (10, groups(("U1", 4))),
                           "funded_recent": (5, groups(("U1", 2), ("U2", 3)))}, TYPES)
        yale = t[t.university == "Yale University"].iloc[0]
        self.assertEqual((yale.role, yale.papers_2005_2023, yale.papers_2024_on, yale.papers_total),
                         ("funded", 4, 2, 6))
        self.assertEqual(t[t.university == "University of Oxford"].iloc[0].university_country, "GB")

    def test_roles_kept_apart(self):
        _, t = setup(ORG, {"funded_past": (1, groups(("U1", 1))), "funded_recent": (0, []),
                           "coauthored_past": (2, groups(("U1", 2))), "coauthored_recent": (0, [])},
                     TYPES)
        self.assertEqual(sorted(t.role), ["coauthored", "funded"])

    def test_company_row_years_and_topics(self):
        raw = [{"id": "https://openalex.org/I1", "topics": [
            {"display_name": "Small topic", "count": 1}, {"display_name": "Big topic", "count": 9}]}]
        c, _ = setup(ORG, {"coauthored_by_year": (12, [{"key": "2024", "count": 5},
                                                       {"key": "2025", "count": 7},
                                                       {"key": "2010", "count": 0}])}, TYPES, raw)
        r = c.iloc[0]
        self.assertEqual((r.papers_since_2005, r.papers_by_year), (12, "2025:7; 2024:5"))
        self.assertEqual(r.topics, "Big topic; Small topic")
        self.assertEqual((r.website, r.city), ("https://acme.com", "Boston"))
        self.assertTrue(r.funded_papers_by_year != r.funded_papers_by_year or r.funded_papers_by_year is None)

    def test_by_year_empty(self):
        self.assertEqual(by_year(None), (None, None))
        self.assertEqual(by_year({"count": 0, "groups": []}), (0, None))


if __name__ == "__main__":
    unittest.main()
