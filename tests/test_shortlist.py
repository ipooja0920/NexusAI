"""Pre-filter places: companies and hospitals/clinics are shortlisted
SEPARATELY, so PT clinics (which only compete for the Hospitals & Clinics
sheet) never take a company's place. Cristina Colón-Semenza: 243 of 510
places had gone to clinics, and Whoop, ReWalk and Sword Health were never
scored."""
import unittest

from match_faculty import combine_searches, shortlist

PROV = {1, 2, 3, 4, 5}                      # indexes that are clinics


def is_prov(gi):
    return gi in PROV


class TestShortlist(unittest.TestCase):

    def test_clinics_do_not_take_company_places(self):
        # by the full profile the clinics rank first
        profile = [1, 2, 3, 4, 5, 10, 11, 12, 13, 14]
        old, _ = combine_searches([("profile", profile)], top_n=4, extra=0)
        self.assertEqual(old, [1, 2, 3, 4])                     # before: all clinics
        chosen, by, n_c, n_p = shortlist([("profile", profile)], is_prov, 4, 0, 2, 0)
        self.assertEqual([g for g in chosen if not is_prov(g)], [10, 11, 12, 13])
        self.assertEqual([g for g in chosen if is_prov(g)], [1, 2])
        self.assertEqual((n_c, n_p), (4, 2))

    def test_keyword_places_split_too(self):
        profile = [10, 1]
        kw = [2, 3, 20, 21, 22]
        chosen, by, n_c, n_p = shortlist([("profile", profile), ("keyword: x", kw)],
                                          is_prov, top_n=1, extra=2, prov_n=1, prov_extra=1)
        self.assertEqual(sorted(g for g in chosen if not is_prov(g)), [10, 20, 21])
        self.assertEqual(sorted(g for g in chosen if is_prov(g)), [1, 2])
        self.assertEqual(by[20], "keyword: x")
        self.assertEqual(by[1], "profile")

    def test_fewer_than_places(self):
        chosen, _, n_c, n_p = shortlist([("profile", [10, 1])], is_prov, 300, 30, 100, 10)
        self.assertEqual((sorted(chosen), n_c, n_p), ([1, 10], 1, 1))


if __name__ == "__main__":
    unittest.main()


class TestClinicFloor(unittest.TestCase):
    """Clinics have their own shortlist places, so a faculty member whose
    work has nothing to do with care would get a sheet full of 1-3 scores:
    a hospital/clinic is listed only from funnel.min_alignment_providers (4)."""

    def test_weak_clinic_not_listed(self):
        import json
        import openpyxl
        from tests.harness import (DEFAULT_COMPANIES, FakeOpenAI, TempProject, _ChatResp,
                                   install_fake_openai)
        install_fake_openai()
        orig = FakeOpenAI._Chat.create

        def create(self_, model, messages):          # the cold clinic scores 3
            if ("how well companies align" in messages[0]["content"].lower()
                    and "Company: Cold Rehab Clinic" in messages[-1]["content"]):
                return _ChatResp(json.dumps({"company": "Cold Rehab Clinic", "score": 3,
                                             "reason": "r"}))
            return orig(self_, model, messages)

        FakeOpenAI._Chat.create = create
        self.addCleanup(setattr, FakeOpenAI._Chat, "create", orig)
        cos = list(DEFAULT_COMPANIES) + [
            ("Hot Rehab Clinic", "20", "3.0", "5 Oak St\nStorrs, Connecticut 06269",
             "Hot Rehab Clinic operates physical therapy clinics and studies zeolite "
             "catalysis in clinical settings.", "www.hotclinic.example"),
            ("Cold Rehab Clinic", "20", "3.0", "6 Oak St\nStorrs, Connecticut 06269",
             "Cold Rehab Clinic operates physical therapy clinics.", "www.coldclinic.example")]
        with TempProject(companies=cos) as p:
            p.seed_geocodes()
            p.run_enrich()
            self.assertEqual(p.run_match(["--faculty", "Ioulia Valla"]), 0)
            wb = openpyxl.load_workbook(p.outputs()[-1])
        hosp = [ws for ws in wb.worksheets if ws.title.startswith("Hospitals")][0]
        listed = [r[1] for r in hosp.iter_rows(min_row=2, values_only=True)]
        self.assertIn("Hot Rehab Clinic", listed)
        self.assertNotIn("Cold Rehab Clinic", listed)
        allc = list(wb["All Companies"].iter_rows(values_only=True))
        h = allc[0]
        cold = [r for r in allc[1:] if r[h.index("Company")] == "Cold Rehab Clinic"][0]
        self.assertEqual(cold[h.index("Organisation Type")], "Clinic / care provider")
        self.assertEqual(cold[h.index("Stage")], "Alignment below 4")
