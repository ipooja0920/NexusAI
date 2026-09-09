"""Tests for nexus/matching.py - the shared company-name matcher.

Every external source (SEC Form D, SBIR, and whatever comes next) joins to
the Capital IQ list through this module, so a bug here silently attributes
one company's money to another across every source at once. These tests are
mostly built from real rows that caused real problems.
"""
from __future__ import annotations

import unittest

from nexus.matching import (TRUSTED_CONFIDENCE, is_too_generic,
                            match_confidence, normalise_company_name,
                            state_code, state_from_address, website_domain)


class TestNormaliseCompanyName(unittest.TestCase):

    def test_spelling_variants_collapse_to_one_key(self):
        """The whole point: SBIR shouts, Capital IQ punctuates."""
        for variant in ("AERODYNE RESEARCH INC", "Aerodyne Research, Inc.",
                        "Aerodyne Research Inc.", "  aerodyne research inc  "):
            self.assertEqual(normalise_company_name(variant), "aerodyne research")

    def test_legal_suffixes_stripped_repeatedly(self):
        self.assertEqual(
            normalise_company_name("Acme Therapeutics Holdings LLC"),
            "acme therapeutics")

    def test_meaningful_words_are_kept(self):
        """'Therapeutics' distinguishes real companies; it must survive."""
        self.assertEqual(normalise_company_name("Halda Therapeutics, LLC"),
                         "halda therapeutics")
        self.assertNotEqual(normalise_company_name("Halda Therapeutics, LLC"),
                            normalise_company_name("Halda Bioscience Inc"))

    def test_openalex_country_tag_removed(self):
        self.assertEqual(normalise_company_name("Pfizer (United States)"),
                         "pfizer")

    def test_ampersand_becomes_and(self):
        self.assertEqual(normalise_company_name("R&D Systems"),
                         normalise_company_name("R and D Systems"))

    def test_empty_and_none(self):
        for bad in (None, "", "   ", float("nan")):
            self.assertEqual(normalise_company_name(bad if bad == bad else None), "")


class TestStateFromAddress(unittest.TestCase):

    def test_street_name_is_not_mistaken_for_a_state(self):
        """Real bug: '201 Washington St., Boston, Massachusetts' -> WA."""
        self.assertEqual(
            state_from_address("201 Washington St., Boston, Massachusetts 02108"),
            "MA")

    def test_takes_the_last_state_not_the_first(self):
        self.assertEqual(
            state_from_address("1 Virginia Ave\nHartford, Connecticut 06103"),
            "CT")

    def test_two_letter_form(self):
        self.assertEqual(state_from_address("100 Main St, Waltham, MA 02451"), "MA")

    def test_no_state_present(self):
        self.assertIsNone(state_from_address("Somewhere, United Kingdom"))
        self.assertIsNone(state_from_address(None))


class TestStateCode(unittest.TestCase):

    def test_full_name_and_abbreviation_agree(self):
        """SBIR writes 'Massachusetts', Form D writes 'MA'."""
        self.assertEqual(state_code("Massachusetts"), state_code("MA"))
        self.assertEqual(state_code("Massachusetts"), "MA")

    def test_case_and_whitespace(self):
        self.assertEqual(state_code("  connecticut "), "CT")
        self.assertEqual(state_code("ct"), "CT")

    def test_two_word_states(self):
        self.assertEqual(state_code("New Hampshire"), "NH")
        self.assertEqual(state_code("District of Columbia"), "DC")

    def test_unusable_input(self):
        for bad in (None, "", "   ", "Ontario", "ZZ"):
            self.assertIsNone(state_code(bad))


class TestWebsiteDomain(unittest.TestCase):

    def test_strips_scheme_www_path_and_query(self):
        for raw in ("https://www.brightspec.com/about?x=1",
                    "http://brightspec.com",
                    "www.BrightSpec.com/",
                    "brightspec.com"):
            self.assertEqual(website_domain(raw), "brightspec.com")

    def test_placeholders_return_none(self):
        for bad in (None, "", "-", "nan", "N/A", "none"):
            self.assertIsNone(website_domain(bad))

    def test_bare_word_is_not_a_domain(self):
        self.assertIsNone(website_domain("acme"))

    def test_port_and_userinfo_removed(self):
        self.assertEqual(website_domain("https://user@acme.com:8080/x"), "acme.com")


class TestMatchConfidence(unittest.TestCase):
    """The rule that decides whether an external record is believed."""

    def test_same_state_is_high(self):
        self.assertEqual(
            match_confidence("statadx", our_state="MA", their_state="MA"), "high")

    def test_short_distinctive_name_survives_a_state_match(self):
        """Real bug: 'statadx' was rejected for being seven characters long
        despite an exact MA/MA agreement. Distinctiveness is not length."""
        self.assertTrue(is_too_generic("acme"))
        self.assertEqual(
            match_confidence("acme", our_state="MA", their_state="MA"), "high")

    def test_relocation_is_not_a_rejection(self):
        """BrightSpec is in MA; its SBIR awards are stamped VA. Same company.
        A state disagreement alone must never veto the match."""
        self.assertEqual(
            match_confidence("brightspec", our_state="MA", their_state="VA"),
            "unverified")
        self.assertIn(
            match_confidence("brightspec", our_state="MA", their_state="VA"),
            TRUSTED_CONFIDENCE)

    def test_matching_website_beats_a_state_disagreement(self):
        """Aprea: Capital IQ says MA, SBIR says PA, both say aprea.com."""
        self.assertEqual(
            match_confidence("aprea therapeutics", our_state="MA",
                             their_state="PA", our_domain="aprea.com",
                             their_domains={"aprea.com"}),
            "high")

    def test_website_agreement_alone_is_enough(self):
        self.assertEqual(
            match_confidence("advanced rna technologies",
                             our_domain="advirna.com",
                             their_domains={"advirna.com"}),
            "high")

    def test_both_signals_disagreeing_is_suspect(self):
        """Different state AND a different website - two companies."""
        self.assertEqual(
            match_confidence("peptech", our_state="MA", their_state="FL",
                             our_domain="peptech-ma.com",
                             their_domains={"peptechcorp.net"}),
            "suspect")
        self.assertNotIn(
            match_confidence("peptech", our_state="MA", their_state="FL",
                             our_domain="peptech-ma.com",
                             their_domains={"peptechcorp.net"}),
            TRUSTED_CONFIDENCE)

    def test_any_of_several_states_may_agree(self):
        """A company with decades of awards appears under several addresses."""
        self.assertEqual(
            match_confidence("mirtech", our_state="MA",
                             their_state={"NJ", "MA"}), "high")

    def test_no_corroboration_but_distinctive_name(self):
        self.assertEqual(
            match_confidence("evolveimmune therapeutics"), "unverified")

    def test_no_corroboration_and_generic_name_is_suspect(self):
        self.assertEqual(match_confidence("acme"), "suspect")
        self.assertEqual(match_confidence(""), "suspect")

    def test_missing_website_on_one_side_is_not_disagreement(self):
        """SBIR fills Company Website only 55.9% of the time. A blank must
        never be read as 'different'."""
        self.assertEqual(
            match_confidence("brightspec", our_state="MA", their_state="VA",
                             our_domain="brightspec.com", their_domains=set()),
            "unverified")
        self.assertEqual(
            match_confidence("brightspec", our_state="MA", their_state="MA",
                             our_domain=None, their_domains={"brightspec.com"}),
            "high")

    def test_suspect_is_excluded_from_trusted(self):
        self.assertEqual(TRUSTED_CONFIDENCE, ("high", "unverified"))


if __name__ == "__main__":
    unittest.main()
