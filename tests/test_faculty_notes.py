"""data/input/faculty_notes.yaml - per-faculty adjustments.

The promise: a faculty member with NO entry gets exactly the output they
got before. One with an entry gets extra research text, a wider "nearby"
(full distance score up to N miles), publications left out by title, and
named leads that are always scored, shown on their own sheet, not penalised
for distance, explained with the researcher's note - and taken from the
all-US list when this run's list does not have them."""
import json
import math
import unittest

import openpyxl
import pandas as pd
import yaml

from nexus import faculty_notes as fn
from tests.harness import FakeOpenAI, TempProject, _ChatResp, install_fake_openai

FACULTY = "Ioulia Valla"
LEAD = "Pyro Energy"                     # "Pyro Energy Inc." is in the test companies


class TestUnits(unittest.TestCase):

    def test_missing_file_is_empty(self):
        self.assertEqual(fn.load_notes("/nonexistent/faculty_notes.yaml"), {})

    def test_bad_field_is_an_error(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("Jane Doe:\n  distance_miles: 400\n")
        try:
            with self.assertRaises(fn.NotesError):
                fn.load_notes(fh.name)
        finally:
            os.unlink(fh.name)

    def test_name_matching(self):
        notes = {"Cristina Colón-Semenza": {"leads": ["X"]}, "Mary Amalaradjou": {}}
        self.assertIsNotNone(fn.notes_for(notes, "Cristina Colon-Semenza"))
        self.assertIsNotNone(fn.notes_for(notes, "Mary Anne Roshni Amalaradjou"))
        self.assertIsNone(fn.notes_for(notes, "Na Li"))

    def test_leads_forms(self):
        e = {"leads": [{"name": "Jazz Pharmaceuticals", "note": "grants"}, "Acme Inc."]}
        self.assertEqual(fn.leads_of(e), [("Jazz Pharmaceuticals", "grants"), ("Acme Inc.", None)])
        self.assertEqual(fn.leads_of(None), [])

    def test_plateau(self):
        self.assertEqual(fn.plateau_distance_score(0, 400, 400), 1.0)
        self.assertEqual(fn.plateau_distance_score(399, 400, 400), 1.0)
        self.assertAlmostEqual(fn.plateau_distance_score(600, 400, 400), math.exp(-0.5))
        self.assertEqual(fn.plateau_distance_score(None, 400, 400), 0.0)

    def test_lead_matching_and_group(self):
        cos = [{"company": "Jazz Pharmaceuticals plc (NasdaqGS:JAZZ)", "parent_company": None},
               {"company": "Jazz Pharmaceuticals (EUSA Pharma USA) Inc.", "parent_company": None},
               {"company": "Greenwich Biosciences, Inc.",
                "parent_company": "Jazz Pharmaceuticals plc"},
               {"company": "Jazzy Foods LLC", "parent_company": None},
               {"company": "Acme Pharma", "parent_company": None}]
        from nexus.groups import build_groups
        self.assertEqual(fn.lead_members("Jazz Pharmaceuticals", cos, build_groups(cos)), [0, 1, 2])

    def test_ignore_publications(self):
        terms = fn.ignore_terms({"ignore_publications": ["HIV", "Stigma"]})
        self.assertFalse(fn.keep_publication("HIV stigma and testing", terms))
        self.assertTrue(fn.keep_publication("Caprine thymoma", terms))


def _sheets(path):
    wb = openpyxl.load_workbook(path)
    out = {}
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if ws.title == "Run Info":
            rows = [r for r in rows if r[0] != "Run date"]
        out[ws.title] = rows
    return out


class TestEndToEnd(unittest.TestCase):

    def setUp(self):
        install_fake_openai()
        FakeOpenAI.reset()

    def _write_notes(self, p, notes):
        path = p.settings().path("faculty_notes")
        path.write_text(yaml.safe_dump(notes, allow_unicode=True), encoding="utf-8")

    def test_no_entry_means_no_change(self):
        with TempProject() as p:
            p.seed_geocodes()
            p.run_enrich()
            self.assertEqual(p.run_match(["--faculty", FACULTY]), 0)
            before = _sheets(p.outputs()[0])
            # a notes file with someone ELSE in it must not change this faculty
            self._write_notes(p, {"Somebody Else": {"distance_full_score_miles": 400,
                                                    "leads": [LEAD]}})
            self.assertEqual(p.run_match(["--faculty", FACULTY]), 0)
            after = _sheets(p.outputs()[-1])
            self.assertEqual(before, after)
            self.assertNotIn("Researcher's leads", after)
            self.assertFalse(any(r[0] == "Faculty notes used" for r in after["Run Info"]))

    def test_entry_applies(self):
        prompts = []
        orig = FakeOpenAI._Chat.create

        def create(self_, model, messages):
            if "justification" in messages[0]["content"].lower():
                prompts.append(messages[-1]["content"])
            return orig(self_, model, messages)

        FakeOpenAI._Chat.create = create
        try:
            with TempProject(config_overrides={"prefilter": {"top_n_candidates": 3}}) as p:
                p.seed_geocodes()
                p.run_enrich()
                self._write_notes(p, {FACULTY: {
                    "extra_research": "Interested in catalyst scale-up with industry.",
                    "distance_full_score_miles": 400,
                    "leads": [{"name": LEAD, "note": "Met them at a conference in June."}]}})
                self.assertEqual(p.run_match(["--faculty", FACULTY]), 0)
                wb = openpyxl.load_workbook(p.outputs()[-1])
        finally:
            FakeOpenAI._Chat.create = orig
        self.assertEqual(wb.sheetnames[0], "Researcher's leads")
        lead = list(wb["Researcher's leads"].iter_rows(values_only=True))
        hdr = lead[0]
        self.assertEqual([r[hdr.index("Company")] for r in lead[1:]], ["Pyro Energy Inc."])
        self.assertEqual(lead[1][hdr.index("Distance Score")], 1.0)
        self.assertTrue(lead[1][hdr.index("Justification")])
        self.assertTrue(any("Their note: Met them at a conference in June." in t for t in prompts))
        info = {r[0]: r[1] for r in wb["Run Info"].iter_rows(values_only=True)}
        self.assertIn("Further notes from the researcher: Interested in catalyst", 
                      info["Research profile used"])
        self.assertIn("lead 'Pyro Energy': 1 company", info["Faculty notes used"])
        self.assertIn("full distance score up to 400 miles", info["Faculty notes used"])
        allc = list(wb["All Companies"].iter_rows(values_only=True))
        h = allc[0]
        dist = [r[h.index("Distance Score")] for r in allc[1:] if r[h.index("Distance (mi)")] is not None]
        self.assertTrue(dist and all(d == 1.0 for d in dist), dist)   # all within 400 miles
        found = {r[h.index("Company")]: r[h.index("Found By")] for r in allc[1:]}
        self.assertIn("like lead: Pyro Energy", set(found.values()))

    def test_lead_from_other_list(self):
        with TempProject() as p:
            p.seed_geocodes()
            p.run_enrich()
            cfg = p.settings()
            df = pd.read_excel(cfg.companies_enriched)
            other = df.iloc[[0]].copy()
            other["company"] = "Zeta Lead Pharma Inc."
            folder = p.dir / "data" / "datasets" / "All US"
            folder.mkdir(parents=True)
            other.to_excel(folder / "companies_enriched.xlsx", index=False, sheet_name="Companies")
            raw = yaml.safe_load(cfg.config_path.read_text(encoding="utf-8"))
            raw["faculty_notes"] = {"lead_sources": [str(folder / "companies_enriched.xlsx")]}
            cfg.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            self._write_notes(p, {FACULTY: {"leads": ["Zeta Lead Pharma"]}})
            self.assertEqual(p.run_match(["--faculty", FACULTY]), 0)
            wb = openpyxl.load_workbook(p.outputs()[-1])
        rows = list(wb["Researcher's leads"].iter_rows(values_only=True))
        self.assertEqual(rows[1][rows[0].index("Company")], "Zeta Lead Pharma Inc.")
        allc = list(wb["All Companies"].iter_rows(values_only=True))
        h = allc[0]
        zeta = [r for r in allc[1:] if r[h.index("Company")] == "Zeta Lead Pharma Inc."][0]
        self.assertEqual(zeta[h.index("Found By")], "researcher's lead (from All US)")
        info = {r[0]: r[1] for r in wb["Run Info"].iter_rows(values_only=True)}
        self.assertIn("(from All US)", info["Faculty notes used"])

    def test_lead_not_found_is_reported(self):
        with TempProject() as p:
            p.seed_geocodes()
            p.run_enrich()
            self._write_notes(p, {FACULTY: {"leads": ["Nowhere Corp"]}})
            self.assertEqual(p.run_match(["--faculty", FACULTY]), 0)
            wb = openpyxl.load_workbook(p.outputs()[-1])
        self.assertNotIn("Researcher's leads", wb.sheetnames)
        info = {r[0]: r[1] for r in wb["Run Info"].iter_rows(values_only=True)}
        self.assertIn("'Nowhere Corp': not found in any company list", info["Faculty notes used"])


if __name__ == "__main__":
    unittest.main()
