"""Tests for the company-matching rule used when attaching SBIR and Form D
to Capital IQ (combine_companies.match_company, nexus.matching.name_parts):

  1. same name (case, spaces, punctuation, a leading "The" ignored; legal
     form must not contradict)                                -> matched
  2. only a similar name (Holdings / Group / ... differ, or LLC vs Inc)
     -> matched only if the websites match, or, with no website to compare,
     the ZIP codes match
  3. very short names count as similar - they must be confirmed too

The cases are the real ones that decided the rule.
"""
from __future__ import annotations

import unittest
from collections import defaultdict

from combine_companies import match_company
from nexus.matching import (name_parts, normalise_company_name, same_name,
                            zip5, zip_from_address)


def same(a, b):
    return same_name(name_parts(a), name_parts(b))


class TestSameName(unittest.TestCase):

    def test_case_spaces_punctuation_ignored(self):
        self.assertTrue(same("NeuroFlow, Inc.", "NEUROFLOW INC"))
        self.assertTrue(same("ABBOTT LABORATORIES", "Abbott Laboratories"))
        self.assertTrue(same("VIP     Play, Inc.", "VIP Play Inc"))

    def test_abbreviated_legal_form_is_the_same(self):
        self.assertTrue(same("Genzyme Corp.", "GENZYME CORPORATION"))
        self.assertTrue(same("THE BOEING COMPANY", "BOEING CO"))
        self.assertTrue(same("The Cannabist Company Holdings Inc.", "Cannabist Co Holdings Inc."))

    def test_missing_legal_form_is_not_the_same(self):
        """Could be a different company - must be confirmed."""
        self.assertFalse(same("Abceutics Inc.", "Abceutics"))
        self.assertTrue(same("ABCEUTICS", "Abceutics"))

    def test_ampersand_and_ticker_tag(self):
        self.assertTrue(same("Cambridge Research & Instrumentation",
                             "CAMBRIDGE RESEARCH AND INSTRUMENTATION"))
        self.assertTrue(same("Organogenesis Holdings Inc. (NasdaqCM:ORGO)",
                             "ORGANOGENESIS HOLDINGS INC"))

    def test_other_bracketed_text_is_part_of_the_name(self):
        self.assertFalse(same("Merck Sharp & Dohme (I.A.) LLC", "Merck Sharp & Dohme LLC"))
        self.assertTrue(same("Curaleaf Holdings, Inc. (TSX:CURA)", "CURALEAF HOLDINGS INC"))

    def test_different_legal_form_is_not_the_same(self):
        self.assertFalse(same("Casimir, LLC", "Casimir, Inc."))

    def test_extra_word_is_not_the_same(self):
        self.assertFalse(same("Curaleaf, Inc.", "Curaleaf Holdings, Inc."))
        self.assertFalse(same("The Mansion Inc", "Mansion Group Inc."))
        self.assertFalse(same("Compass US", "Compass, Inc."))
        self.assertFalse(same("Dickinson Group, LLC", "Dickinson Corp"))

    def test_empty(self):
        self.assertFalse(same("", ""))
        self.assertFalse(same(None, "Acme"))


class TestZip(unittest.TestCase):

    def test_capital_iq_block(self):
        a = ("Headquarters\n100 West Industry Court \nDeer Park, New York    11729\n"
             "United States\nMain Phone: (631) 242-9729")
        self.assertEqual(zip_from_address(a), "11729")

    def test_phone_number_not_taken_for_zip(self):
        a = "Headquarters\nBoston, Massachusetts\nUnited States\nMain Phone: 617 555 12345"
        self.assertIsNone(zip_from_address(a))

    def test_zip5(self):
        self.assertEqual(zip5("06269-1234"), "06269")
        self.assertIsNone(zip5("6269"))
        self.assertIsNone(zip5(None))


def addr(zipcode):
    return f"Headquarters\n1 Main St \nStorrs, Connecticut    {zipcode}\nUnited States"


def source(*companies):
    """companies: (key, name, domains, zips) -> (data, by_name, by_domain)."""
    data, by_name, by_domain = {}, defaultdict(list), {}
    for key, name, domains, zips in companies:
        data[key] = {"name": name, "exacts": {name_parts(name)},
                     "domains": set(domains), "zips": set(zips)}
        by_name[normalise_company_name(name)].append(key)
        for d in domains:
            by_domain[d] = key
    return data, by_name, by_domain


class TestRule(unittest.TestCase):

    def match(self, name, website, zipcode, *companies):
        data, by_name, by_domain = source(*companies)
        return match_company(name, website, addr(zipcode) if zipcode else None,
                             data, by_name, by_domain)[:2]

    # 1. same name
    def test_same_name_matches_without_checks(self):
        self.assertEqual(self.match("Genzyme Corp.", None, None,
                                    ("k", "GENZYME CORPORATION", [], [])), ("k", "name"))

    def test_same_name_matches_even_if_websites_differ(self):
        """NeuroFlow: Capital IQ lists its parent's website."""
        self.assertEqual(self.match("NeuroFlow, Inc.", "quartethealth.com", None,
                                    ("k", "NEUROFLOW INC", ["neuroflow.com"], [])),
                         ("k", "name"))

    # 2. similar name
    def test_similar_name_confirmed_by_website(self):
        self.assertEqual(self.match("Covaris, LLC", "covaris.com", None,
                                    ("k", "COVARIS, INC.", ["covaris.com"], [])),
                         ("k", "name+website"))

    def test_similar_name_different_websites_is_different_company(self):
        """Casimir, LLC (casimirtrials.com) is not CASIMIR, INC (casimirspace.com),
        even at the same ZIP - the website decides when both have one."""
        self.assertEqual(self.match("Casimir, LLC", "casimirtrials.com", "06269",
                                    ("k", "CASIMIR, INC", ["casimirspace.com"], ["06269"])),
                         (None, "unconfirmed"))

    def test_similar_name_confirmed_by_zip_when_no_website(self):
        self.assertEqual(self.match("MimiVax, LLC", None, "14203",
                                    ("k", "MIMIVAX INC.", [], ["14203"])),
                         ("k", "name+address"))

    def test_similar_name_zip_differs(self):
        self.assertEqual(self.match("Curaleaf, Inc.", None, "02453",
                                    ("k", "Curaleaf Holdings, Inc.", [], ["10001"])),
                         (None, "unconfirmed"))

    def test_similar_name_nothing_to_compare(self):
        self.assertEqual(self.match("Compass US", None, None,
                                    ("k", "Compass, Inc.", [], ["10011"])),
                         (None, "unconfirmed"))

    # 3. short names
    def test_short_same_name_needs_confirmation(self):
        """Meta, Inc. on Long Island is not the SBIR 'Meta, Inc.' in San Jose."""
        self.assertEqual(self.match("Meta, Inc.", None, "11530",
                                    ("k", "META INC", [], ["95125"])),
                         (None, "unconfirmed"))
        self.assertEqual(self.match("Meta, Inc.", None, "95125",
                                    ("k", "META INC", [], ["95125"])),
                         ("k", "name+address"))

    def test_missing_legal_form_needs_confirmation(self):
        self.assertEqual(self.match("Abceutics Inc.", None, "02139",
                                    ("k", "Abceutics", [], ["94107"])),
                         (None, "unconfirmed"))
        self.assertEqual(self.match("Abceutics Inc.", None, "02139",
                                    ("k", "Abceutics", [], ["02139"])),
                         ("k", "name+address"))

    # namesakes and website-only
    def test_two_namesakes_zip_picks_one(self):
        self.assertEqual(self.match("Adelphi Technology Inc", None, "40502",
                                    ("ky", "ADELPHI TECHNOLOGY INC", [], ["40502"]),
                                    ("ca", "Adelphi Technology, Inc.", [], ["94301"])),
                         ("ky", "name+address"))

    def test_two_namesakes_nothing_picks(self):
        self.assertEqual(self.match("Adelphi Technology Inc", None, None,
                                    ("ky", "ADELPHI TECHNOLOGY INC", [], ["40502"]),
                                    ("ca", "Adelphi Technology, Inc.", [], ["94301"])),
                         (None, "ambiguous"))

    def test_website_only_catches_a_rename(self):
        self.assertEqual(self.match("NewName Bio, Inc.", "oldname.com", None,
                                    ("k", "OLDNAME INC", ["oldname.com"], [])),
                         ("k", "website"))

    def test_no_match(self):
        self.assertEqual(self.match("Nobody Inc", "nobody.com", "06269",
                                    ("k", "Somebody Inc", ["somebody.com"], ["06269"])),
                         (None, None))


if __name__ == "__main__":
    unittest.main()
