"""The six fixes from the review of Julie Burland's first results:
  1 NA partnership = neutral, and the partnership prompt sees the track record
  2 acquired / out-of-business / liquidating companies left out; subsidiaries
    labelled with their parent
  3 hospitals and clinics labelled (not removed)
  4 min alignment for a top tab
  5 pre-filter searches each keyword too
  6 an implausibly small SEC R&D figure is ignored
"""
import tempfile
import unittest
from pathlib import Path

import openpyxl
import pandas as pd

from match_faculty import (combine_searches, partnership_text, prefilter_queries, run_pass,
                           track_record_text)
from nexus.cache import Caches
from nexus.funding import company_funding_score
from nexus.organisation import CLINIC, COMPANY, HOSPITAL, organisation_type, parent_company
from nexus.settings import Settings
from tests.harness import FakeOpenAI, TempProject, install_fake_openai


class TestOrganisation(unittest.TestCase):

    def test_real_capital_iq_wording(self):
        self.assertEqual(organisation_type(
            "New York Society for the Relief of the Ruptured and Crippled",
            "New York Society for the Relief of the Ruptured and Crippled operates as "
            "hospital in New York City."), HOSPITAL)
        self.assertEqual(organisation_type(
            "Hackensack Meridian Health, Inc.", "Hackensack Meridian Health, Inc., through "
            "its subsidiaries, operates a health care network in New Jersey."), HOSPITAL)
        self.assertEqual(organisation_type("Sports & Rehab Medicine Pc", "Ofcs Clns Of Mdl D"),
                         CLINIC)
        self.assertEqual(organisation_type(
            "Orthopedic Health Services Inc", "Operates As A Provider Of Managed Care, "
            "Physical Therapy And Other Fitness And Health Services (100%)"), CLINIC)

    def test_a_dental_supply_distributor_is_a_company(self):
        self.assertEqual(organisation_type("Dental Supplies Co.",
                                           "Distributor of dental equipment and consumables."),
                         COMPANY)

    def test_animal_hospital_is_a_clinic(self):
        self.assertEqual(organisation_type("Floyd Harbor Animal Hospital",
                                           "Floyd Harbor Animal Hospital is a health care "
                                           "facilities company."), CLINIC)

    def test_makers_are_companies_whatever_the_wording(self):
        self.assertEqual(organisation_type(
            "ZetrOZ Systems LLC", "ZetrOZ Systems LLC develops and manufactures wearable, "
            "non-invasive medical devices. Used in hospitals and clinics."), COMPANY)
        # "clinical" is not "clinic"
        self.assertEqual(organisation_type(
            "Mariel Therapeutics, Inc.", "Mariel Therapeutics, Inc. is a clinical stage "
            "therapeutic company."), COMPANY)
        # a full stop inside the name does not cut the description short
        self.assertEqual(organisation_type("Sanofi-Aventis U.S. LLC",
                                           "Sanofi-Aventis U.S. LLC develops medicines."), COMPANY)

    def test_parent(self):
        self.assertEqual(parent_company(
            "Biorez, Inc. develops scaffold implants. As of 2022, Biorez, Inc. operates as a "
            "subsidiary of CONMED Corporation."), "CONMED Corporation")
        self.assertIsNone(parent_company("Miach Orthopaedics, Inc. develops implants."))


class TestRdSanity(unittest.TestCase):

    def setUp(self):
        self.cfg = Settings().funding

    def test_tiny_rd_for_a_giant_is_ignored(self):
        score, reason = company_funding_score(
            {"xbrl_rd_expense_usdmm": 109, "xbrl_rd_year": 2025, "revenue_usdmm": 97929}, self.cfg)
        self.assertEqual(score, self.cfg["missing_score"])
        self.assertIn("ignored", reason)

    def test_normal_rd_kept(self):
        score, reason = company_funding_score(
            {"xbrl_rd_expense_usdmm": 26, "xbrl_rd_year": 2025, "revenue_usdmm": 120}, self.cfg)
        self.assertGreater(score, self.cfg["missing_score"])
        self.assertIn("$26m R&D", reason)

    def test_small_company_never_checked(self):
        # under revenue_min the ratio says nothing (a startup can have tiny revenue)
        reason = company_funding_score(
            {"xbrl_rd_expense_usdmm": 2, "xbrl_rd_year": 2025, "revenue_usdmm": 900}, self.cfg)[1]
        self.assertNotIn("ignored", reason)


class TestTrackRecord(unittest.TestCase):

    def test_text(self):
        t = track_record_text({"university_past": "Yale University; MIT", "sbir_awarded": "Yes",
                               "oa_coauthored_university": 1.0, "oa_funded_university": 0.0,
                               "ct_university_current": float("nan")})
        self.assertIn("Past university partners on SBIR/STTR awards: Yale University; MIT", t)
        self.assertIn("Co-wrote research papers with universities", t)
        self.assertNotIn("Paid for", t)
        self.assertNotIn("Current", t)
        self.assertIn("none found", track_record_text({}))
        self.assertTrue(partnership_text({}, "Makes braces.").startswith("Makes braces."))


class TestPrefilterQueries(unittest.TestCase):

    def test_keywords_and_interests_searched(self):
        fac = {"source": "registration", "keywords": "biomechanics, wearables; gait",
               "notes": "Companies testing wearables in our lab."}
        labels = [q[0] for q in prefilter_queries(fac, "PROFILE")]
        self.assertEqual(labels, ["profile", "keyword: biomechanics", "keyword: wearables",
                                  "keyword: gait", "industry interests"])
        self.assertEqual(prefilter_queries({"research": "x"}, "P"), [("profile", "P")])

    def test_profile_keeps_all_its_places_and_searches_add(self):
        profile = list(range(100))                       # its own ranking
        kw = [5, 3, 200, 201, 202, 203]                  # 5 and 3 already picked
        chosen, by = combine_searches([("profile", profile), ("kw", kw)], top_n=10, extra=3)
        self.assertEqual(chosen[:10], list(range(10)))   # the profile top 10, untouched
        self.assertEqual(chosen[10:], [200, 201, 202])   # 3 NEW ones added on top
        self.assertEqual(by[200], "kw")
        self.assertEqual(by[5], "profile")


def _company(name, dist=0.5):
    return {"company": name, "distance_score": dist, "employee_score": 0.1, "revenue_score": 0.5}


class TestRunPass(unittest.TestCase):

    def setUp(self):
        self.cfg = Settings()
        self.caches = Caches(Path(tempfile.mkdtemp()))

    def test_na_partnership_is_neutral_and_min_alignment(self):
        self.cfg.raw["funnel"]["min_alignment"] = 5
        comps = [_company("A"), _company("B"), _company("C"), _company("Low", dist=1.0)]
        align = {0: {"score": "8", "reason": ""}, 1: {"score": "8", "reason": ""},
                 2: {"score": "8", "reason": ""}, 3: {"score": "3", "reason": ""}}
        partner = {0: {"score": "9", "reason": ""}, 1: {"score": "3", "reason": ""},
                   2: {"score": "NA", "reason": ""}}
        p = run_pass([0, 1, 2, 3], comps, ["t"] * 4, align, partner, self.cfg,
                     self.caches, client=None)
        self.assertNotIn(3, p["final"])               # 3/9 alignment never listed
        self.assertNotIn(3, p["secondary"])
        o = p["overall"]
        self.assertGreater(o[0], o[2])                # 9 beats unknown
        self.assertGreater(o[2], o[1])                # unknown beats 3
        w = self.cfg.weights["partnership"]
        self.assertAlmostEqual(o[2] - o[1], w * 0.5, places=6)


class TestPipeline(unittest.TestCase):

    def setUp(self):
        install_fake_openai()

    def test_left_out_status_and_labels(self):
        p = TempProject(config_overrides={"prefilter": {"top_n_candidates": 3}})
        p.__enter__()
        try:
            p.seed_geocodes()
            p.run_enrich()
            cfg = p.settings()
            df = pd.read_excel(cfg.companies_enriched)
            df["company_status"] = df["company_status"].astype(object)
            df.loc[df.company == "Catalyst Works LLC", "company_status"] = "Acquired"
            df.loc[df.company == "Pyro Energy Inc.", "company_status"] = "Operating Subsidiary"
            # labels are recomputed from the description at match time
            df["description"] = df["description"].astype(object)
            m = df.company == "Pyro Energy Inc."
            df.loc[m, "description"] = (df.loc[m, "description"].fillna("") +
                                        " Pyro Energy Inc. operates as a subsidiary of Big Energy Corp.")
            with pd.ExcelWriter(cfg.companies_enriched) as w:
                df.to_excel(w, index=False, sheet_name="Companies")
            FakeOpenAI.reset()
            self.assertEqual(p.run_match(), 0)
            wb = openpyxl.load_workbook(p.outputs()[0])
            for ws in wb.worksheets:
                if ws.title.startswith(("Small", "Mid", "Large")):
                    names = [ws.cell(r, 2).value for r in range(2, ws.max_row + 1)]
                    self.assertNotIn("Catalyst Works LLC", names)
            ws = wb["All Companies"]
            hdr = [c.value for c in ws[1]]
            rows = {ws.cell(r, 3).value: [ws.cell(r, k + 1).value for k in range(len(hdr))]
                    for r in range(2, ws.max_row + 1)}
            self.assertEqual(rows["Catalyst Works LLC"][0], "Left out (Acquired)")
            pyro = dict(zip(hdr, rows["Pyro Energy Inc."]))
            self.assertEqual(pyro["Status"], "Operating Subsidiary")
            self.assertEqual(pyro["Parent Company"], "Big Energy Corp")
            self.assertEqual(pyro["Organisation Type"], "Company")
            self.assertIn("Found By", hdr)
        finally:
            p.__exit__()

    def test_weak_matches_never_fill_a_tab(self):
        faculty = [("Prof Dental", "X", "Y", "orthodontic enamel bonding", "", "", "N")]
        # the floor is optional (off by default) - switched on here
        p = TempProject(faculty=faculty, config_overrides={"funnel": {
            "min_alignment": 5, "min_alignment_by_tier": {"Small": 5, "Mid": 5, "Large": 5}}})
        p.__enter__()
        try:
            p.seed_geocodes()
            p.run_enrich()
            # the stub scores 8 only where the company text has a catalysis
            # word; everyone else 3/9 - and those must never be listed
            self.assertEqual(p.run_match(), 0)
            wb = openpyxl.load_workbook(p.outputs()[0])
            for ws in wb.worksheets:
                if ws.title.startswith(("Small", "Mid", "Large")):
                    col = [c.value for c in ws[1]].index("Alignment Score (1-9)") + 1
                    scores = [int(ws.cell(r, col).value) for r in range(2, ws.max_row + 1)]
                    self.assertTrue(all(s >= 5 for s in scores), (ws.title, scores))
            ws = wb["All Companies"]
            stage = {ws.cell(r, 3).value: ws.cell(r, 1).value for r in range(2, ws.max_row + 1)}
            self.assertEqual(stage["Dental Supplies Co."], "Alignment below 5")
        finally:
            p.__exit__()


if __name__ == "__main__":
    unittest.main()


class TestReviewFixes(unittest.TestCase):
    """From Julie Burland's review: methods over condition, a floor for Mid
    and Large, and justifications that start with the verdict."""

    def test_alignment_prompt_ranks_methods_over_condition(self):
        from nexus.scoring import ALIGNMENT_SYSTEM_PROMPT as P
        self.assertIn("HOW the researcher works", P)
        self.assertIn("different approach", P)
        self.assertIn("unrelated company scores 1-2, not NA", P)

    def test_tier_minimums(self):
        from match_faculty import tier_min_alignment
        cfg = Settings()
        self.assertEqual(tier_min_alignment(cfg, "Small"), 0)
        self.assertEqual(tier_min_alignment(cfg, "Mid"), 4)
        self.assertEqual(tier_min_alignment(cfg, "Large"), 4)

    def test_verdict(self):
        from match_faculty import verdict
        self.assertEqual([verdict(x) for x in ("9", "7", "6", "5", "4", "1", "NA")],
                         ["Strong fit", "Strong fit", "Moderate fit", "Moderate fit",
                          "Weak fit", "Weak fit", "Fit unclear"])

    def test_mid_large_floor_in_a_run(self):
        """Every company listed in Mid / Large scored at least 4; Small has no floor."""
        install_fake_openai()
        faculty = [("Prof Dental", "X", "Y", "orthodontic enamel bonding", "", "", "N")]
        p = TempProject(faculty=faculty)
        p.__enter__()
        try:
            p.seed_geocodes()
            p.run_enrich()
            self.assertEqual(p.run_match(), 0)
            wb = openpyxl.load_workbook(p.outputs()[0])
            mid = wb["Mid ($100m-$1bn) - top 50"]
            col = [c.value for c in mid[1]].index("Alignment Score (1-9)") + 1
            self.assertTrue(all(int(mid.cell(r, col).value) >= 4 for r in range(2, mid.max_row + 1)))
            j = [c.value for c in mid[1]].index("Justification") + 1
            for ws in wb.worksheets[:2]:
                for r in range(2, ws.max_row + 1):
                    self.assertTrue(str(ws.cell(r, j).value).split(":")[0] in
                                    ("Strong fit", "Moderate fit", "Weak fit", "Fit unclear"))
        finally:
            p.__exit__()
