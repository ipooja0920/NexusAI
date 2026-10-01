"""SBIR awards with no UEI (older ones) are grouped into companies by the
matching rule's name - legal form included - so two different companies
that share a word are not merged ("PHT Corporation", Boston, and "PHT,
Inc.", Pittsburgh)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from combine_companies import SBIR_READ, summarise_sbir


def awards(rows):
    df = pd.DataFrame([{c: "" for c in SBIR_READ} | r for r in rows])
    path = Path(tempfile.mkdtemp()) / "sbir_clean.csv"
    df.to_csv(path, index=False)
    return summarise_sbir(path)


class TestNoUEIGrouping(unittest.TestCase):

    def test_different_legal_forms_are_two_companies(self):
        s = awards([
            {"company": "PHT CORPORATION", "agency": "HHS", "award_year": "2003", "zip": "02129"},
            {"company": "PHT, Inc.", "agency": "DOD", "award_year": "1996", "zip": "15235"},
        ])
        self.assertEqual(len(s), 2)
        self.assertEqual(sorted(v["sbir_agencies"] for v in s.values()), ["DOD (1)", "HHS (1)"])

    def test_same_name_spelt_differently_is_one_company(self):
        s = awards([
            {"company": "ACME THERAPEUTICS INC", "agency": "HHS", "award_year": "2003"},
            {"company": "Acme Therapeutics, Inc.", "agency": "HHS", "award_year": "2004"},
        ])
        self.assertEqual(len(s), 1)

    def test_uei_still_groups_whatever_the_name(self):
        s = awards([
            {"company": "OLDNAME INC", "uei": "U1", "agency": "HHS", "award_year": "2010"},
            {"company": "NewName, LLC", "uei": "U1", "agency": "HHS", "award_year": "2020"},
        ])
        self.assertEqual(len(s), 1)


if __name__ == "__main__":
    unittest.main()
