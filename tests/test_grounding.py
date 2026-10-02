"""No step may add facts that are not in our data: the prompts say so, and
the justification is checked automatically - a figure or university name
that is not in the researcher profile or the company facts sends it back
once, and if it still invents, a plain data-only justification is used."""
import json
import unittest

from nexus.scoring import (ALIGNMENT_SYSTEM_PROMPT, JUSTIFICATION_SYSTEM_PROMPT,
                           PARTNERSHIP_SYSTEM_PROMPT, ungrounded, write_justification)
from nexus.company_profile import PROFILE_SYSTEM_PROMPT


class _Resp:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


class Scripted:
    """A client that answers with the given texts in order."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.chat = self
        self.completions = self

    def create(self, model, messages):
        self.prompts.append(messages[-1]["content"])
        return _Resp(json.dumps({"justification": self.answers.pop(0)}))


FACTS = ("Distance from UConn Storrs: 63 miles\n"
         "Money available: $4.6m of SBIR/STTR awards running.\n"
         "University track record (SBIR, ClinicalTrials.gov, OpenAlex):\n"
         "- Past university partners on SBIR/STTR awards: University of Miami")


class TestCheck(unittest.TestCase):

    def test_grounded_text_passes(self):
        self.assertEqual(ungrounded("63 miles away, $4.6m of SBIR awards, alignment 8/9, "
                                    "past partner University of Miami.", FACTS), [])

    def test_invented_figures_and_universities_caught(self):
        bad = ungrounded("Raised $12m in 2021 and works with Harvard University.", FACTS)
        self.assertEqual(bad, ["12", "2021", "Harvard University"])


class TestWriteJustification(unittest.TestCase):

    def test_rewrite_once_then_accept(self):
        client = Scripted(["Partners with Harvard University.",
                           "Past partner University of Miami; 63 miles away."])
        out = write_justification(client, "m", "biomechanics", "ZetrOZ", FACTS)
        self.assertEqual(out, "Past partner University of Miami; 63 miles away.")
        self.assertIn("Harvard University", client.prompts[1])     # told what was wrong

    def test_still_inventing_gives_none(self):
        client = Scripted(["Partners with Harvard University.", "Raised $40m in 2020."])
        self.assertIsNone(write_justification(client, "m", "biomechanics", "ZetrOZ", FACTS))


class TestPromptsForbidOutsideKnowledge(unittest.TestCase):

    def test_every_prompt(self):
        for p in (ALIGNMENT_SYSTEM_PROMPT, PARTNERSHIP_SYSTEM_PROMPT):
            self.assertIn("Do not use outside knowledge", p)
            self.assertIn("ONLY", p)
        self.assertIn("Do not use outside knowledge", JUSTIFICATION_SYSTEM_PROMPT)
        self.assertIn("ONLY the source material", PROFILE_SYSTEM_PROMPT)
        self.assertNotIn("reliably know", PARTNERSHIP_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
