"""Tests for the company-matching rule used when attaching SBIR and Form D
to Capital IQ (combine_companies.match_company, nexus.matching.name_parts):

  1. same name (case, spaces, punctuation, a leading "The" ignored; legal
     form must not contradict)                                -> matched
  2. only a similar name (Holdings / Group / ... differ, or LLC vs Inc)
     -> matched only if the websites match, or, with no website to compare,
     the ZIP codes match
  3. names of 3 characters or fewer count as similar - they must be confirmed too

The cases are the real ones that decided the rule.
"""
from __future__ import annotations

import unittest
from collections import defaultdict

from combine_companies import all_name_keys, match_company, name_index
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

    def test_bracketed_text_ignored(self):
        self.assertTrue(same("AlaMab Therapeutics (Shanghai) Inc.", "AlaMab Therapeutics Inc."))
        self.assertTrue(same("Merck Sharp & Dohme (I.A.) LLC", "Merck Sharp & Dohme LLC"))
        self.assertTrue(same("Curaleaf Holdings, Inc. (TSX:CURA)", "CURALEAF HOLDINGS INC"))

    def test_country_in_the_name_ignored(self):
        self.assertTrue(same("Zydus Pharmaceuticals USA, Inc.", "Zydus Pharmaceuticals (USA) Inc."))
        self.assertTrue(same("Legend Biotech USA Inc.", "Legend Biotech Inc."))
        self.assertTrue(same("Acme U.S.A., Inc.", "Acme, Inc."))
        self.assertTrue(same("Acme China Ltd", "ACME LIMITED"))

    def test_country_as_first_word_kept(self):
        self.assertFalse(same("China Resources Inc.", "Resources Inc."))
        self.assertFalse(same("US Oncology Inc.", "Oncology Inc."))

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
    data, by_domain = {}, {}
    for key, name, domains, zips in companies:
        names = name if isinstance(name, list) else [name]   # a renamed company
        data[key] = {"name": names[-1], "exacts": {name_parts(n) for n in names},
                     "keys": all_name_keys(names),
                     "domains": set(domains), "zips": set(zips)}
        for d in domains:
            by_domain[d] = key
    return data, name_index(data), by_domain


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
    def test_acronym_same_name_needs_confirmation(self):
        """3 characters or fewer: HSS, Inc. in New York is not every HSS."""
        self.assertEqual(self.match("HSS, Inc.", None, "10021",
                                    ("k", "HSS INC", [], ["80112"])),
                         (None, "unconfirmed"))
        self.assertEqual(self.match("HSS, Inc.", None, "80112",
                                    ("k", "HSS INC", [], ["80112"])),
                         ("k", "name+address"))

    def test_short_but_not_acronym_same_name_matches(self):
        """Your rule: an identical name is the same company."""
        self.assertEqual(self.match("Abyrx, Inc.", None, None,
                                    ("k", "Abyrx, Inc.", [], [])), ("k", "name"))
        self.assertEqual(self.match("Eisai Inc.", None, None,
                                    ("k", "Eisai Inc.", [], [])), ("k", "name"))

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

    def test_country_in_either_name_is_ignored(self):
        """'Anchiano Therapeutics Israel Ltd.' (trial sponsor) is Capital IQ's
        'Anchiano Therapeutics Ltd.' - the country word is not the name."""
        self.assertEqual(self.match("Anchiano Therapeutics Ltd.", None, None,
                                    ("k", "Anchiano Therapeutics Israel Ltd.", [], [])),
                         ("k", "name"))
        self.assertEqual(self.match("Anchiano Therapeutics Israel Ltd.", None, None,
                                    ("k", "Anchiano Therapeutics Ltd.", [], [])),
                         ("k", "name"))

    def test_renamed_company_found_by_its_old_name(self):
        """Form D: one CIK filed as Durin Technologies, then Durin Life
        Sciences. Capital IQ still lists the old name."""
        self.assertEqual(self.match("Durin Technologies, Inc.", None, None,
                                    ("cik", ["Durin Technologies, Inc.",
                                             "Durin Life Sciences, Inc."], [], [])),
                         ("cik", "name"))

    def test_no_match(self):
        self.assertEqual(self.match("Nobody Inc", "nobody.com", "06269",
                                    ("k", "Somebody Inc", ["somebody.com"], ["06269"])),
                         (None, None))


if __name__ == "__main__":
    unittest.main()


class TestCapitalIQDuplicates(unittest.TestCase):
    """combine_companies.drop_duplicate_companies: a repeated Capital IQ
    company is kept once ONLY when it is known to be the same company -
    same name AND same website (or, with no website to compare, same ZIP)."""

    def run_(self, rows):
        import pandas as pd
        from combine_companies import drop_duplicate_companies
        df = pd.DataFrame([(n, w, addr(z) if z else "-", r) for n, w, z, r in rows],
                          columns=["Company Name", "Website", "Offices", "Revenue"])
        return drop_duplicate_companies(df, "Company Name", "Website", "Offices")

    def test_same_name_same_website_kept_once_most_complete(self):
        kept, removed = self.run_([["Acme Bio, Inc.", "www.acme.com", "06269", "-"],
                                   ["ACME BIO INC", "acme.com", "10001", "12.5"]])
        self.assertEqual(list(kept["Revenue"]), ["12.5"])
        self.assertEqual(len(removed), 1)

    def test_same_name_no_website_same_zip_kept_once(self):
        """Adgero: same street, suite and ZIP, listed as Pittstown and Princeton."""
        kept, _ = self.run_([["Adgero Biopharmaceuticals Holdings, Inc.", "-", "08540", "-"],
                             ["Adgero Biopharmaceuticals Holdings, Inc.", "-", "08540", "-"]])
        self.assertEqual(len(kept), 1)

    def test_same_name_different_websites_stay_separate(self):
        kept, removed = self.run_([["Beacon Hospice Inc.", "beaconhospice.com", "06108", "-"],
                                   ["Beacon Hospice, Inc.", "amedisys.com", "04210", "-"]])
        self.assertEqual((len(kept), len(removed)), (2, 0))

    def test_same_name_nothing_to_compare_stays_separate(self):
        kept, _ = self.run_([["Allcare Medical Inc.", "-", "08872", "-"],
                             ["Allcare Medical Inc.", "-", "11803", "-"]])
        self.assertEqual(len(kept), 2)

    def test_different_names_never_merged(self):
        kept, _ = self.run_([["Casimir, LLC", "casimir.com", "06269", "-"],
                             ["Casimir, Inc.", "casimir.com", "06269", "-"]])
        self.assertEqual(len(kept), 2)

    def test_tie_keeps_the_first_and_order_is_kept(self):
        kept, _ = self.run_([["Zeta Inc.", "-", "-", "-"],
                             ["Acme Bio, Inc.", "acme.com", "-", "-"],
                             ["ACME BIO INC", "acme.com", "-", "-"]])
        self.assertEqual(list(kept["Company Name"]), ["Zeta Inc.", "Acme Bio, Inc."])
