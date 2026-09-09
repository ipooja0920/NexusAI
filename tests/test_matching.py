"""Tests for nexus/matching.py - the shared company-name matcher.

Every external source (SEC Form D, SBIR, and whatever comes next) joins to
the Capital IQ list through this module, so a bug here silently attributes
one company's money to another across every source at once. These tests are
mostly built from real rows that caused real problems.
"""
from __future__ import annotations

import unittest

from nexus.matching import (TRUSTED_CONFIDENCE, domains_related,
                            is_too_generic, match_confidence,
                            normalise_company_name, state_code,
                            state_from_address, website_domain)


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

    def test_social_and_directory_urls_are_not_company_websites(self):
        """SBIR lists linkedin.com as Xsphera's website. Treating that as a
        domain would make every such company appear to share a site."""
        for junk in ("https://www.linkedin.com/company/xsphera",
                     "facebook.com/acme", "crunchbase.com/organization/acme"):
            self.assertIsNone(website_domain(junk))

    def test_port_and_userinfo_removed(self):
        self.assertEqual(website_domain("https://user@acme.com:8080/x"), "acme.com")


class TestDomainsRelated(unittest.TestCase):
    """Both cases below are real rows from the SBIR match."""

    def test_suffixed_domain_is_the_same_company(self):
        """nflectionrx.com redirects to nflection.com - one company."""
        self.assertTrue(domains_related("nflection.com", "nflectionrx.com"))
        self.assertTrue(domains_related("nflectionrx.com", "nflection.com"))

    def test_shared_prefix_alone_is_not_enough(self):
        """casimirtrials.com runs clinical trials; casimirspace.com builds
        space hardware. They share 'casimir' and nothing else."""
        self.assertFalse(domains_related("casimirtrials.com",
                                         "casimirspace.com"))

    def test_identical(self):
        self.assertTrue(domains_related("aprea.com", "aprea.com"))

    def test_real_containment_pairs(self):
        """Every pair below is a real company in the datasource whose
        Capital IQ website and SBIR website differ."""
        for a, b in (("elucid.com", "elucidbio.com"),
                     ("excision.bio", "excisionbio.com"),
                     ("javelinbio.com", "javelinbiotech.com"),
                     ("ginkgo.bio", "ginkgobioworks.com"),
                     ("cytositebio.com", "cytositebiopharma.net")):
            self.assertTrue(domains_related(a, b), f"{a} / {b}")

    def test_same_stem_different_tld(self):
        """Bitome is bitome.net in Capital IQ and bitome.com in SBIR."""
        self.assertTrue(domains_related("bitome.net", "bitome.com"))

    def test_subdomain_is_ignored(self):
        """New England Biolabs: international.neb.com vs neb.com."""
        self.assertTrue(domains_related("international.neb.com", "neb.com"))
        self.assertTrue(domains_related("s.fdsonics.com", "fdsonics.com"))

    def test_hyphen_is_punctuation_not_a_difference(self):
        """E-TrolZ is etrolz.com in Capital IQ, e-trolz.com in SBIR."""
        self.assertTrue(domains_related("etrolz.com", "e-trolz.com"))

    def test_short_stems_prove_nothing(self):
        """'bio' inside 'biotech' must not merge two companies."""
        self.assertFalse(domains_related("bio.com", "biotech.com"))

    def test_unrelated(self):
        self.assertFalse(domains_related("acme.com", "globex.com"))

    def test_missing_values(self):
        self.assertFalse(domains_related(None, "acme.com"))
        self.assertFalse(domains_related("acme.com", None))
        self.assertFalse(domains_related(None, None))


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

        A differing state carries no weight at all: sources freeze the
        address as at the time of the record, so a company that moved will
        always disagree. With no website to check, the name match stands.
        """
        self.assertEqual(
            match_confidence("brightspec", our_state="MA", their_state="VA"),
            "high")
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

    def test_related_domain_rescues_a_state_disagreement(self):
        """NFlection: MA here, PA in SBIR, nflectionrx.com vs nflection.com.
        Same company - a suffixed domain must not read as a mismatch."""
        self.assertEqual(
            match_confidence("nflection therapeutics", our_state="MA",
                             their_state="PA", our_domain="nflectionrx.com",
                             their_domains={"nflection.com"}),
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
        """Nothing to check against, but 'evolveimmune therapeutics' is not
        a name two companies land on by accident."""
        self.assertEqual(
            match_confidence("evolveimmune therapeutics"), "high")

    def test_no_corroboration_and_generic_name_is_suspect(self):
        """The one case a name match cannot carry on its own: a name common
        enough that two companies could plausibly share it, with no state
        and no website to settle it."""
        self.assertEqual(match_confidence("acme"), "suspect")
        self.assertEqual(match_confidence(""), "suspect")

    def test_a_generic_name_is_rescued_by_a_matching_state(self):
        self.assertEqual(
            match_confidence("acme", our_state="MA", their_state="MA"), "high")

    def test_only_a_differing_website_rejects(self):
        """Casimir: same name, casimirtrials.com here, casimirspace.com in
        SBIR, and the states differ too. Nothing agrees, so it is rejected."""
        self.assertEqual(
            match_confidence("casimir", our_state="MA", their_state="TX",
                             our_domain="casimirtrials.com",
                             their_domains={"casimirspace.com"}),
            "suspect")

    def test_a_rebrand_keeps_its_match_when_the_state_agrees(self):
        """Lyra Therapeutics was 480 Biomedical and never left MA. Five real
        companies rebranded to a new domain while staying put, so a
        differing website must not override a state that agrees."""
        self.assertEqual(
            match_confidence("lyra therapeutics", our_state="MA",
                             their_state="MA",
                             our_domain="lyratherapeutics.com",
                             their_domains={"480biomedical.com"}),
            "high")

    def test_missing_website_on_one_side_is_not_disagreement(self):
        """SBIR fills Company Website only 55.9% of the time. A blank must
        never be read as 'different' - it means 'nothing to check'."""
        self.assertEqual(
            match_confidence("brightspec", our_state="MA", their_state="VA",
                             our_domain="brightspec.com", their_domains=set()),
            "high")
        self.assertEqual(
            match_confidence("brightspec", our_state="MA", their_state="MA",
                             our_domain=None, their_domains={"brightspec.com"}),
            "high")

    def test_suspect_is_excluded_from_trusted(self):
        self.assertEqual(TRUSTED_CONFIDENCE, ("high", "unverified"))


if __name__ == "__main__":
    unittest.main()
