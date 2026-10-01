"""End-to-end pipeline tests, run against a throwaway project.

The AI client and geocoder are stubbed, so these tests need no API key and
make no network calls. They verify the behaviours that matter operationally:
flags flip, interrupted runs resume, caches are reused, one bad faculty
doesn't sink the rest, and --dry-run really spends nothing.
"""
import unittest

import openpyxl

from tests.harness import FakeOpenAI, TempProject, install_fake_openai


def top_sheet(path, which="all companies"):
    """The Top Matches tab of one pass ('pre-filter' or 'all companies')."""
    wb = openpyxl.load_workbook(path)
    name = next(n for n in wb.sheetnames if n.startswith("Top Matches") and which in n)
    return wb[name]


def column(ws, header):
    """1-based column number of a header."""
    return [c.value for c in ws[1]].index(header) + 1


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        install_fake_openai()

    @staticmethod
    def _prepared(**kwargs):
        """A temp project with geocodes already cached (no network needed)."""
        p = TempProject(**kwargs)
        p.__enter__()
        p.seed_geocodes()
        return p


class TestFullRun(PipelineTestCase):
    def test_enrich_then_match_produces_output(self):
        p = self._prepared()
        try:
            self.assertEqual(p.run_enrich(), 0)
            self.assertTrue(p.settings().companies_enriched.exists())

            self.assertEqual(p.run_match(), 0)
            outputs = p.outputs()
            self.assertEqual(len(outputs), 1)
            self.assertIn("Ioulia Valla", outputs[0].name)

            wb = openpyxl.load_workbook(outputs[0])
            self.assertTrue(any(n.startswith("Top Matches") for n in wb.sheetnames))
            self.assertIn("All Companies", wb.sheetnames)
            self.assertIn("Run Info", wb.sheetnames)
        finally:
            p.__exit__()

    def test_flag_flips_to_Y(self):
        p = self._prepared()
        try:
            p.run_enrich()
            self.assertEqual(p.faculty_flags()["Ioulia Valla"], "N")
            p.run_match()
            self.assertEqual(p.faculty_flags()["Ioulia Valla"], "Y")
        finally:
            p.__exit__()

    def test_relevant_companies_outrank_irrelevant_ones(self):
        """Sanity check on the whole scoring chain: a catalysis company
        should beat a dental supplier for a catalysis professor."""
        p = self._prepared()
        try:
            p.run_enrich()
            p.run_match()
            ws = top_sheet(p.outputs()[0])
            ranked = [ws.cell(r, 2).value for r in range(2, ws.max_row + 1)]
            self.assertIn("Catalyst Works LLC", ranked)
            pos = {name: i for i, name in enumerate(ranked)}
            for good in ["Catalyst Works LLC", "Pyro Energy Inc."]:
                for bad in ["Dental Supplies Co.", "Quiet Insurance Group"]:
                    if good in pos and bad in pos:
                        self.assertLess(pos[good], pos[bad],
                                        f"{good} should outrank {bad}")
        finally:
            p.__exit__()

    def test_all_companies_sheet_covers_every_company(self):
        p = self._prepared()
        try:
            p.run_enrich()
            p.run_match()
            ws = openpyxl.load_workbook(p.outputs()[0])["All Companies"]
            self.assertEqual(ws.max_row - 1, 8)          # all 8 fixtures present
            stages = {ws.cell(r, 1).value for r in range(2, ws.max_row + 1)}
            self.assertTrue(stages <= {"Top 20", "Secondary scored (top 40)",
                                       "Alignment scored", "Filtered out (pre-filter)",
                                       "Not scored (no description)"})
        finally:
            p.__exit__()


class TestResumeAndSelection(PipelineTestCase):
    def test_second_run_skips_completed_faculty(self):
        p = self._prepared()
        try:
            p.run_enrich()
            p.run_match()
            self.assertEqual(len(p.outputs()), 1)
            # Re-run: nothing left with Flag = N
            self.assertEqual(p.run_match(), 0)
            self.assertEqual(len(p.outputs()), 1, "should not re-process a Y faculty")
        finally:
            p.__exit__()

    def test_faculty_flag_overrides_and_reruns(self):
        p = self._prepared()
        try:
            p.run_enrich()
            p.run_match()
            p.run_match(["--faculty", "Ioulia Valla"])
            self.assertEqual(len(p.outputs()), 2, "--faculty should ignore the Y flag")
        finally:
            p.__exit__()

    def test_unknown_faculty_name_errors_cleanly(self):
        p = self._prepared()
        try:
            p.run_enrich()
            rc = p.run_match(["--faculty", "Nobody At All"])
            self.assertEqual(rc, 1)
            self.assertEqual(len(p.outputs()), 0)
        finally:
            p.__exit__()

    def test_dry_run_spends_no_api_calls(self):
        p = self._prepared()
        try:
            p.run_enrich()
            FakeOpenAI.reset()
            rc = p.run_match(["--dry-run"])
            self.assertEqual(rc, 0)
            self.assertEqual(FakeOpenAI.counts(), {}, "dry-run must call no APIs")
            self.assertEqual(len(p.outputs()), 0)
            self.assertEqual(p.faculty_flags()["Ioulia Valla"], "N")
        finally:
            p.__exit__()

    def test_multiple_faculty_each_get_a_file(self):
        faculty = [
            ("Prof One", "CoE", "CBE", "catalysis and zeolite materials", "Energy", "", "N"),
            ("Prof Two", "Business", "Marketing", "consumer retail analytics", "Retail", "", "N"),
            ("Prof Three", "CoE", "CBE", "pyrolysis and fuels", "Energy", "", "Y"),
        ]
        p = self._prepared(faculty=faculty)
        try:
            p.run_enrich()
            p.run_match()
            names = [o.name for o in p.outputs()]
            self.assertEqual(len(names), 2, "only the two Flag=N faculty run")
            self.assertTrue(any("Prof One" in n for n in names))
            self.assertTrue(any("Prof Two" in n for n in names))
            flags = p.faculty_flags()
            self.assertEqual(flags["Prof One"], "Y")
            self.assertEqual(flags["Prof Two"], "Y")
        finally:
            p.__exit__()


class TestCaching(PipelineTestCase):
    def test_secondary_scores_reused_across_faculty(self):
        """Partnership/funding answers don't depend on the professor, so the
        second faculty should trigger far fewer of those calls."""
        faculty = [
            ("Prof One", "CoE", "CBE", "catalysis and zeolites", "Energy", "", "N"),
            ("Prof Two", "CoE", "CBE", "catalysis and fuels", "Energy", "", "N"),
        ]
        p = self._prepared(faculty=faculty)
        try:
            p.run_enrich()
            FakeOpenAI.reset()
            p.run_match()
            counts = FakeOpenAI.counts()
            # 7 companies with a description x 2 faculty = 14 alignment calls
            # (Unknown Holdings has none and is not sent to the AI)
            self.assertEqual(counts.get("alignment", 0), 14)
            # secondary scored once per company only, not once per faculty
            self.assertEqual(counts.get("partnership", 0), 8)
            # Funding is arithmetic now, not a second LLM call. Zero here is
            # the point: it halves the API spend of a match run.
            self.assertEqual(counts.get("funding", 0), 0)
        finally:
            p.__exit__()

    def test_re_running_enrich_uses_cache_and_makes_no_new_calls(self):
        p = self._prepared()
        try:
            p.run_enrich()
            FakeOpenAI.reset()
            p.run_enrich()
            self.assertEqual(FakeOpenAI.counts().get("embedding", 0), 0,
                             "second enrichment should be fully cached")
        finally:
            p.__exit__()

    def test_changed_description_re_embeds(self):
        """Cache is keyed by text hash, so edited descriptions refresh."""
        p = self._prepared()
        try:
            p.run_enrich()
            cfg = p.settings()
            wb = openpyxl.load_workbook(cfg.companies_raw)
            # K is the Business Description column - see harness._write_companies,
            # which mirrors the real Capital IQ export layout.
            wb.active["K9"] = "Completely rewritten description about pyrolysis."
            wb.save(cfg.companies_raw)
            FakeOpenAI.reset()
            p.run_enrich()
            self.assertEqual(FakeOpenAI.counts().get("embedding", 0), 1,
                             "only the edited company should re-embed")
        finally:
            p.__exit__()


class TestEnrichedCarriesTheFundingScore(PipelineTestCase):
    """Every score that needs no API call belongs in the enriched sheet,
    beside distance_score and revenue_score - not only in a faculty output."""

    def test_funding_score_and_reason_are_written(self):
        p = self._prepared()
        try:
            p.run_enrich()
            cfg = p.settings()
            wb = openpyxl.load_workbook(cfg.companies_enriched)
            ws = wb["Companies"]
            hdr = [c.value for c in ws[1]]
            self.assertIn("funding_score", hdr)
            self.assertIn("funding_reason", hdr)
            col = hdr.index("funding_score") + 1
            scores = [ws.cell(r, col).value for r in range(2, ws.max_row + 1)]
            self.assertTrue(all(s is not None for s in scores))
            self.assertTrue(all(0.0 <= float(s) <= 1.0 for s in scores))
        finally:
            p.__exit__()

    def test_the_reason_names_the_evidence(self):
        """A bare 0.42 with nothing behind it is less useful than the LLM
        sentence it replaced, so the reason has to say what it saw."""
        p = self._prepared()
        try:
            p.run_enrich()
            cfg = p.settings()
            ws = openpyxl.load_workbook(cfg.companies_enriched)["Companies"]
            hdr = [c.value for c in ws[1]]
            col = hdr.index("funding_reason") + 1
            reasons = [ws.cell(r, col).value for r in range(2, ws.max_row + 1)]
            self.assertTrue(all(r and str(r).strip() for r in reasons))
            # the fixture has no source files, so every company is neutral
            self.assertTrue(all("neutral" in str(r) for r in reasons))
        finally:
            p.__exit__()


class TestPrefilterActivation(PipelineTestCase):
    def test_prefilter_inactive_when_fewer_companies_than_cutoff(self):
        p = self._prepared()          # 8 companies, cutoff 300
        try:
            p.run_enrich()
            FakeOpenAI.reset()
            p.run_match()
            counts = FakeOpenAI.counts()
            self.assertEqual(counts.get("alignment", 0), 7,
                             "every company with a description scored")
            self.assertEqual(counts.get("embedding", 0), 0,
                             "faculty profile should not be embedded when filter is off")
        finally:
            p.__exit__()

    def test_prefilter_active_when_cutoff_is_small(self):
        p = self._prepared(config_overrides={"prefilter": {"top_n_candidates": 3}})
        try:
            p.run_enrich()
            FakeOpenAI.reset()
            p.run_match()
            counts = FakeOpenAI.counts()
            # pass 1 scores the 3 candidates; pass 2 the other 4 with a
            # description - the 3 come from the cache, not a second call
            self.assertEqual(counts.get("alignment", 0), 7)
            self.assertEqual(counts.get("embedding", 0), 1, "faculty profile embedded once")
            wb = openpyxl.load_workbook(p.outputs()[0])
            self.assertIn("Top Matches - 3 pre-filter", wb.sheetnames)
            self.assertIn("Top Matches - all companies", wb.sheetnames)
            ws = wb["All Companies"]
            inpf = column(ws, "In 300 Pre-filter")
            self.assertEqual([ws.cell(r, inpf).value for r in range(2, ws.max_row + 1)].count("Yes"), 3)
        finally:
            p.__exit__()

    def test_no_full_pass_only_scores_candidates(self):
        p = self._prepared(config_overrides={"prefilter": {"top_n_candidates": 3}})
        try:
            p.run_enrich()
            FakeOpenAI.reset()
            p.run_match(["--no-full"])
            self.assertEqual(FakeOpenAI.counts().get("alignment", 0), 3, "only candidates scored")
            ws = openpyxl.load_workbook(p.outputs()[0])["All Companies"]
            stages = [ws.cell(r, 1).value for r in range(2, ws.max_row + 1)]
            self.assertEqual(stages.count("Filtered out (pre-filter)")
                             + stages.count("Not scored (no description)"), 5)
        finally:
            p.__exit__()

    def test_prefilter_keeps_the_relevant_companies(self):
        p = self._prepared(config_overrides={"prefilter": {"top_n_candidates": 3}})
        try:
            p.run_enrich()
            p.run_match()
            ws = openpyxl.load_workbook(p.outputs()[0])["All Companies"]
            inpf = column(ws, "In 300 Pre-filter")
            kept = {ws.cell(r, 3).value for r in range(2, ws.max_row + 1)
                    if ws.cell(r, inpf).value == "Yes"}
            self.assertIn("Catalyst Works LLC", kept)
            self.assertNotIn("Dental Supplies Co.", kept)
        finally:
            p.__exit__()


class TestRobustness(PipelineTestCase):
    def test_na_and_malformed_ai_responses_do_not_crash(self):
        """'Unknown Holdings' returns NA; 'Broken Data Corp' returns junk.
        Both must be handled without stopping the run."""
        p = self._prepared()
        try:
            p.run_enrich()
            self.assertEqual(p.run_match(), 0)
            ws = openpyxl.load_workbook(p.outputs()[0])["All Companies"]
            col = column(ws, "Alignment Score (1-9)")
            rows = {ws.cell(r, 3).value: ws.cell(r, col).value
                    for r in range(2, ws.max_row + 1)}
            self.assertEqual(rows["Unknown Holdings Inc."], "NA")
            self.assertEqual(rows["Broken Data Corp."], "NA")
        finally:
            p.__exit__()

    def test_match_without_enrichment_fails_clearly(self):
        p = self._prepared()
        try:
            with self.assertRaises(FileNotFoundError) as ctx:
                p.run_match()
            self.assertIn("enrich_companies.py", str(ctx.exception))
        finally:
            p.__exit__()

    def test_company_with_no_geocode_still_scored(self):
        """A failed geocode must not remove a company from the run;
        it just loses the distance component."""
        companies = [("No Address Co.", "10", "40", None,
                      "Zeolite catalyst manufacturer.", "-")]
        p = TempProject(companies=companies)
        p.__enter__()
        try:
            p.run_enrich(["--skip-geocode"])
            p.run_match()
            ws = top_sheet(p.outputs()[0])
            self.assertEqual(ws.cell(2, 2).value, "No Address Co.")
            self.assertIsNone(ws.cell(2, column(ws, "Distance (mi)")).value)
            self.assertEqual(ws.cell(2, column(ws, "Distance Score")).value, 0)
        finally:
            p.__exit__()

    def test_empty_faculty_list_exits_cleanly(self):
        faculty = [("Done Prof", "S", "D", "research", "C1", "C2", "Y")]
        p = self._prepared(faculty=faculty)
        try:
            p.run_enrich()
            self.assertEqual(p.run_match(), 0)
            self.assertEqual(len(p.outputs()), 0)
        finally:
            p.__exit__()


if __name__ == "__main__":
    unittest.main()
