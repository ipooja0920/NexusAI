"""AI company profile: built only where SBIR / trial / OpenAlex text exists,
from the Capital IQ description plus that text, cached by its input."""
import tempfile
import unittest
from pathlib import Path

from nexus.cache import Caches
from nexus.company_profile import (ABSTRACTS_MAX, PROFILE_SYSTEM_PROMPT, build_profiles,
                                   has_sources, source_text)
from tests.harness import FakeOpenAI, install_fake_openai


class TestSourceText(unittest.TestCase):

    def test_only_with_extra_sources(self):
        self.assertFalse(has_sources({"sbir_abstracts": float("nan"), "oa_topics": "-"}))
        self.assertTrue(has_sources({"ct_conditions": "Knee Injuries"}))

    def test_starts_from_capital_iq_and_caps_abstracts(self):
        row = {"sbir_abstracts": "#" * (ABSTRACTS_MAX + 500), "ct_treatment_types": "Device",
               "oa_topics": "Gait analysis"}
        text = source_text("Acme", "Acme makes knee braces.", row)
        self.assertTrue(text.startswith("Company: Acme\n\nCapital IQ description: Acme makes knee braces."))
        self.assertIn("Treatment types in its clinical trials: Device", text)
        self.assertIn("Gait analysis", text)
        self.assertEqual(text.count("#"), ABSTRACTS_MAX)

    def test_prompt_forbids_names_and_invention(self):
        self.assertIn("Do not name any people", PROFILE_SYSTEM_PROMPT)
        self.assertIn("ONLY the source material", PROFILE_SYSTEM_PROMPT)


class TestBuild(unittest.TestCase):

    def setUp(self):
        install_fake_openai()

    def test_cached_second_time(self):
        import openai
        caches = Caches(Path(tempfile.mkdtemp()))
        jobs = {0: source_text("Acme", "Knee braces.", {"oa_topics": "Gait"}),
                5: source_text("Beta", "Drugs.", {"ct_conditions": "Asthma"})}
        out = build_profiles(openai.OpenAI(), "m", jobs, caches, workers=2)
        self.assertEqual(set(out), {0, 5})
        self.assertIn("Gait", out[0])
        self.assertEqual(FakeOpenAI.counts().get("profile"), 2)
        FakeOpenAI.reset()
        again = build_profiles(openai.OpenAI(), "m", jobs, caches, workers=2)
        self.assertEqual(again, out)
        self.assertEqual(FakeOpenAI.counts().get("profile", 0), 0)


if __name__ == "__main__":
    unittest.main()


class TestEnrichWritesProfiles(unittest.TestCase):

    def setUp(self):
        install_fake_openai()

    def test_profile_column_and_its_embedding(self):
        import pandas as pd
        from tests.harness import TempProject
        with TempProject() as p:
            p.seed_geocodes()
            p.run_combine()
            cfg = p.settings()
            path = cfg.path("companies_combined")
            df = pd.read_excel(path, sheet_name="Companies")
            df["oa_topics"] = df["oa_topics"].astype(object)
            df.loc[df["Company Name"] == "Dental Supplies Co.", "oa_topics"] = "Gait analysis"
            with pd.ExcelWriter(path) as w:
                df.to_excel(w, index=False, sheet_name="Companies")
            FakeOpenAI.reset()
            self.assertEqual(p._run("enrich_companies", "enrich_companies.py", []), 0)
            self.assertEqual(FakeOpenAI.counts().get("profile"), 1)
            e = pd.read_excel(cfg.companies_enriched).set_index("company")
            self.assertIn("Gait analysis", e.loc["Dental Supplies Co.", "company_profile"])
            self.assertTrue(e.loc["Dental Supplies Co.", "profile_from"].startswith("AI"))
            self.assertEqual(e.loc["Catalyst Works LLC", "profile_from"], "Capital IQ description")
            self.assertEqual(e.loc["Catalyst Works LLC", "company_profile"],
                             e.loc["Catalyst Works LLC", "description"])
