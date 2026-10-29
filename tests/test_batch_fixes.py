"""Fixes from the Na Li / Sahar / Chathura / Leonel / Yuyang QA round:

1. justifications speak ABOUT the researcher, never as them ("my work")
2. a parent written two ways ("Lonza Group Ltd" / "Lonza Group AG") is one
   corporate group - but "Merck KGaA" is still not "Merck & Co."
3. a company with no website on record gets a "check it still operates" note
4. a company that SELLS veterinary products is a company, not a vet clinic
5. same-name Capital IQ records at different addresses stay separate rows
"""
import json
import unittest

import pandas as pd

from combine_companies import drop_duplicate_companies
from match_faculty import NO_WEBSITE_NOTE, has_website, plain_justification, with_website_note
from nexus.groups import build_groups, parent_key
from nexus.organisation import CLINIC, COMPANY, organisation_type
from nexus.scoring import JUSTIFICATION_SYSTEM_PROMPT, first_person, write_justification


class _Resp:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


class Scripted:
    """A client that answers with the given justifications in order."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.chat = self
        self.completions = self

    def create(self, model, messages):
        self.prompts.append(messages[-1]["content"])
        return _Resp(json.dumps({"justification": self.answers.pop(0)}))


FACTS = "Distance from UConn Storrs: 63 miles"
PROFILE = "Research description: My research develops chip-scale photonics."


class TestFirstPerson(unittest.TestCase):

    def test_found(self):
        for text in ["Strong fit: My work on fluorescent probes aligns with Akoya.",
                     "Overlap: my recombinant probiotic vaccine work",
                     "I develop integrated photonics", "our lab", "we build"]:
            self.assertTrue(first_person(text), text)

    def test_not_first_person(self):
        for text in ["Strong fit: the researcher's photonics work aligns.",
                     "Your work on lasers aligns with Kopin.",
                     "Phase I trials", "Type I diabetes", "Class I-II devices",
                     "oxLink I and II reagents", "I-V curves", "US-based company",
                     "Merck KGaA"]:
            self.assertEqual(first_person(text), [], text)

    def test_prompt_says_so(self):
        self.assertIn("Never write as the researcher", JUSTIFICATION_SYSTEM_PROMPT)

    def test_rewritten_once(self):
        client = Scripted(["Strong fit: my photonics work aligns; 63 miles away.",
                           "Strong fit: your photonics work aligns; 63 miles away."])
        out = write_justification(client, "m", PROFILE, "Kopin", FACTS)
        self.assertEqual(out, "Strong fit: your photonics work aligns; 63 miles away.")
        self.assertIn("written as the researcher (my)", client.prompts[1])

    def test_still_first_person_gives_plain(self):
        client = Scripted(["Strong fit: my photonics work aligns.",
                           "Strong fit: I think my work fits."])
        self.assertIsNone(write_justification(client, "m", PROFILE, "Kopin", FACTS))

    def test_both_problems_named(self):
        client = Scripted(["Strong fit: my work; partners with Harvard University.",
                           "Strong fit: the researcher's work; 63 miles away."])
        out = write_justification(client, "m", PROFILE, "Kopin", FACTS)
        self.assertEqual(out, "Strong fit: the researcher's work; 63 miles away.")
        self.assertIn("Harvard University", client.prompts[1])
        self.assertIn("written as the researcher", client.prompts[1])


class TestParentNames(unittest.TestCase):

    def test_same_parent(self):
        for a, b in [("Lonza Group Ltd", "Lonza Group AG (SWX:LONN)"),
                     ("Evotec AG", "Evotec SE"),
                     ("TE Connectivity Ltd", "TE Connectivity plc"),
                     ("Nokia Corporation", "Nokia Oyj"),
                     ("Novo Nordisk A/S", "Novo Nordisk"),
                     ("Sanofi S.A.", "Sanofi"),
                     ("Bayer Aktiengesellschaft", "Bayer AG")]:
            self.assertEqual(parent_key(a), parent_key(b), (a, b))

    def test_still_apart(self):
        self.assertNotEqual(parent_key("Merck KGaA"), parent_key("Merck & Co., Inc."))
        self.assertEqual(parent_key("SA Recycling LLC"), "sa recycling")   # SA at the front stays

    def test_capsugel_joins_lonza(self):
        cos = [{"company": "Lonza Group AG (SWX:LONN)", "parent_company": None},
               {"company": "Capsugel, Inc.", "parent_company": "Lonza Group Ltd"},
               {"company": "Lonza Biologics Inc.", "parent_company": "Lonza Group AG"},
               {"company": "Merck KGaA", "parent_company": None},
               {"company": "Merck Animal Health", "parent_company": "Merck & Co., Inc."}]
        g = build_groups(cos)
        self.assertIsNotNone(g[0])
        self.assertEqual(g[0], g[1])
        self.assertEqual(g[0], g[2])
        self.assertTrue(g[3] is None or g[3] != g[4])      # Merck KGaA not with Merck & Co.


class TestNoWebsite(unittest.TestCase):

    def test_has_website(self):
        self.assertTrue(has_website({"website": "www.kopin.com"}))
        for v in [None, "", "-", "nan", " - "]:
            self.assertFalse(has_website({"website": v}), v)

    def test_note_added_once(self):
        c = {"company": "CoreTek, Inc.", "website": "-"}
        text = with_website_note("Strong fit: tunable lasers.", c)
        self.assertTrue(text.endswith(NO_WEBSITE_NOTE))
        self.assertTrue(text.startswith("Strong fit:"))           # verdict still first
        self.assertEqual(with_website_note(text, c), text)

    def test_no_note_with_website(self):
        c = {"company": "Kopin", "website": "www.kopin.com"}
        self.assertEqual(with_website_note("Strong fit: x.", c), "Strong fit: x.")

    def test_plain_justification_gets_it_too(self):
        c = {"company": "Multiplex, Inc.", "website": None, "distance_miles": 142}
        plain = plain_justification(c, {"score": 8, "reason": "photonic components"},
                                    (0.5, "No record."))
        self.assertTrue(with_website_note(plain, c).endswith(NO_WEBSITE_NOTE))


class TestVetProducts(unittest.TestCase):

    def test_sellers_are_companies(self):
        for name, desc in [
                ("Biotech Laboratories Usa LLC",
                 "Biotech Laboratories USA LLC provides high quality veterinary diagnostic "
                 "products to veterinarians, livestock producers, and pet owners."),
                ("Boehringer Ingelheim Animal Health USA Inc.",
                 "Boehringer Ingelheim Animal Health USA Inc. provides veterinary products "
                 "and services. The company offers vaccines, therapeutics."),
                ("Virbac AH, Inc.", "Virbac AH, Inc. offers animal health products.")]:
            self.assertEqual(organisation_type(name, desc), COMPANY, name)

    def test_care_settings_still_clinics(self):
        self.assertEqual(organisation_type(
            "Main Street Animal Hospital", "Provides veterinary care for pets."), CLINIC)
        self.assertEqual(organisation_type(
            "Health Pet", "Health Pet operates veterinary medicine facilities."), CLINIC)


class TestSameNameRecords(unittest.TestCase):
    """Two Capital IQ records with one name but different websites and
    addresses are two companies (Hi-Tech Metals in Bellingham MA and in
    Maspeth NY) - combining keeps both; the real repeat is dropped."""

    def test_kept_apart_and_repeat_dropped(self):
        df = pd.DataFrame({
            "name": ["Hi-Tech Metals, Inc.", "Hi-Tech Metals, Inc.", "Kopin Corporation",
                     "Kopin Corporation"],
            "site": ["www.algonquinindustries.com", "www.hi-techmetals.com",
                     "www.kopin.com", "kopin.com"],
            "addr": ["139 Farm Street, Bellingham, Massachusetts 02019",
                     "5920 56th Ave, Maspeth, New York 11378",
                     "125 North Drive, Westborough, Massachusetts 01581",
                     "125 North Drive, Westborough, Massachusetts 01581"]})
        kept, removed = drop_duplicate_companies(df, "name", "site", "addr")
        self.assertEqual(list(kept["name"]).count("Hi-Tech Metals, Inc."), 2)
        self.assertEqual(list(kept["name"]).count("Kopin Corporation"), 1)
        self.assertEqual(len(removed), 1)


if __name__ == "__main__":
    unittest.main()
