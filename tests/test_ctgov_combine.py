"""Tests for the ClinicalTrials.gov columns in combine_companies.py
(summarise_ctgov, _ranked) and how a company is found. Made-up rows."""
from __future__ import annotations

import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

import pandas as pd

from combine_companies import _ranked, match_company, summarise_ctgov


def rows(*trials):
    """trials: dicts with company, nct_id, status, is_active, phase_number,
    start_year, conditions, intervention_types, partners (defaults filled)."""
    base = dict(company="Acme Bio, Inc.", status="RECRUITING", is_active=True,
                phase_number=2, start_year=2025, conditions="Obesity",
                intervention_types="DRUG", partners=None)
    f = Path(tempfile.mkdtemp()) / "ct.csv"
    pd.DataFrame([{**base, "nct_id": f"NCT{i}", **t} for i, t in enumerate(trials)]).to_csv(f, index=False)
    return summarise_ctgov(f)


def one(summary):
    assert len(summary) == 1, summary.keys()
    return next(iter(summary.values()))


class TestRanked(unittest.TestCase):

    def test_most_frequent_first_and_capped(self):
        text, full = _ranked(["b; a", "a", "c"] + [f"x{i}" for i in range(20)], top=3)
        self.assertTrue(text.startswith("a; b; c") or text.startswith("a; b;"))
        self.assertIn("(+", text)
        self.assertEqual(len(full), 23)

    def test_exclude_and_empty(self):
        self.assertEqual(_ranked(["a; b"], exclude={"a"})[0], "b")
        self.assertEqual(_ranked([None, float("nan")]), (None, set()))


class TestSummary(unittest.TestCase):

    def test_current_and_past_university(self):
        s = one(rows(dict(partners="Yale University"),
                     dict(status="COMPLETED", is_active=False, partners="Yale University; Duke University")))
        self.assertEqual(s["ct_university_current"], "Yale University")
        self.assertEqual(s["ct_university_past"], "Duke University")   # Yale is current

    def test_withdrawn_trials_left_out_entirely(self):
        s = one(rows(dict(status="COMPLETED", is_active=False),
                     dict(status="WITHDRAWN", is_active=False, partners="Yale University",
                          start_year=2026, conditions="Asthma")))
        self.assertIsNone(s["ct_university_past"])
        self.assertEqual(s["ct_last_start_year"], 2025)
        self.assertEqual(s["ct_conditions"], "Obesity")

    def test_active_count_and_highest_active_phase(self):
        s = one(rows(dict(phase_number=2), dict(phase_number=3),
                     dict(status="COMPLETED", is_active=False, phase_number=4)))
        self.assertEqual((s["ct_trials_active"], s["ct_active_phase"]), (2, "Phase 3"))

    def test_no_running_trial(self):
        s = one(rows(dict(status="COMPLETED", is_active=False)))
        self.assertEqual((s["ct_trials_active"], s["ct_active_phase"]), (0, None))

    def test_treatment_types_readable_and_other_left_out(self):
        s = one(rows(dict(intervention_types="DEVICE; OTHER"), dict(intervention_types="DEVICE"),
                     dict(intervention_types="DRUG")))
        self.assertEqual(s["ct_treatment_types"], "Device; Drug")

    def test_spellings_of_the_same_name_are_one_company(self):
        s = one(rows(dict(company="Abiomed Inc."), dict(company="ABIOMED, INC")))
        self.assertEqual(s["ct_trials_active"], 2)

    def test_different_legal_forms_stay_separate(self):
        self.assertEqual(len(rows(dict(company="Casimir, LLC"), dict(company="Casimir, Inc."))), 2)


class TestMatch(unittest.TestCase):

    def match(self, name, summary):
        by_name = defaultdict(list)
        for k, v in summary.items():
            by_name[v["norm"]].append(k)
        return match_company(name, None, None, summary, by_name)[:2]

    def test_same_name_matches(self):
        s = rows(dict(company="Abiomed Inc."))
        self.assertEqual(self.match("Abiomed, Inc. (NasdaqGS:ABMD)", s)[1], "name")

    def test_missing_legal_form_stays_unconfirmed(self):
        """No website or address in trial records - nothing can confirm it."""
        s = rows(dict(company="Acorda Therapeutics"))
        self.assertEqual(self.match("Acorda Therapeutics, Inc.", s), (None, "unconfirmed"))

    def test_bracketed_entity_is_a_different_name(self):
        s = rows(dict(company="Merck Sharp & Dohme LLC"))
        self.assertEqual(self.match("Merck Sharp & Dohme (I.A.) LLC", s), (None, "unconfirmed"))


if __name__ == "__main__":
    unittest.main()
