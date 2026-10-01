"""Tests for fetch_openalex_ties.py with the network call replaced."""
from __future__ import annotations

import unittest

import fetch_openalex_ties as F


class TestQuestions(unittest.TestCase):

    def test_funded_and_coauthored_filters(self):
        q = F.questions({"funder": "F1", "institution": "I1"}, 2026)
        self.assertEqual(q["funded_past"],
                         ("awards.funder_id:F1,publication_year:2005-2023", "authorships.institutions.id"))
        self.assertEqual(q["funded_recent"][0], "awards.funder_id:F1,publication_year:2024-2026")
        self.assertEqual(q["coauthored_by_year"],
                         ("authorships.institutions.lineage:I1,publication_year:2005-2026", "publication_year"))
        self.assertEqual(len(q), 6)

    def test_only_the_roles_the_company_has(self):
        self.assertEqual(set(F.questions({"funder": None, "institution": "I1"}, 2026)),
                         {"coauthored_past", "coauthored_recent", "coauthored_by_year"})
        self.assertEqual(set(F.questions({"funder": "F1", "institution": None}, 2026)),
                         {"funded_past", "funded_recent", "funded_by_year"})


class TestGroupBy(unittest.TestCase):

    def setUp(self):
        self._get, self._pause = F.get, F.PAUSE
        F.PAUSE = 0

    def tearDown(self):
        F.get, F.PAUSE = self._get, self._pause

    def test_follows_the_cursor_and_keeps_every_group(self):
        pages = {"*": {"meta": {"count": 9, "next_cursor": "c2"},
                       "group_by": [{"key": "https://openalex.org/I1", "count": 5}]},
                 "c2": {"meta": {"count": 9, "next_cursor": None},
                        "group_by": [{"key": "https://openalex.org/I2", "count": 4}]}}
        F.get = lambda path, params, key: pages[params["cursor"]]
        r = F.group_by("x", "authorships.institutions.id", "k")
        self.assertEqual(r["count"], 9)
        self.assertEqual([g["count"] for g in r["groups"]], [5, 4])

    def test_out_of_budget_is_raised(self):
        def full(*a):
            raise F.OutOfBudget("429")
        F.get = full
        with self.assertRaises(F.OutOfBudget):
            F.group_by("x", "publication_year", "k")

    def test_short_id(self):
        self.assertEqual(F.short_id("https://openalex.org/I123"), "I123")


if __name__ == "__main__":
    unittest.main()
