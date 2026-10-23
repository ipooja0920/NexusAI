"""Every justification carries exactly one verdict - "Strong fit",
"Moderate fit" or "Weak fit" ("Fit unclear" for an NA score) - at the
start, and it is the one the alignment score gives (7-9, 5-6, 1-4), whatever
label the AI wrote itself."""
import hashlib
import json
import re
import unittest

import openpyxl

from match_faculty import VERDICTS, plain_justification, verdict, with_verdict
from tests.harness import FakeOpenAI, TempProject, _ChatResp, install_fake_openai

PHRASE = re.compile(r"(?i)\b(strong fit|moderate fit|weak fit|fit unclear)\b")


def check(test, text, score):
    """Starts with the right verdict, and names no other verdict anywhere."""
    test.assertTrue(text.startswith(verdict(score) + ": "), (score, text[:80]))
    found = {m.lower() for m in PHRASE.findall(text)}
    test.assertEqual(found, {verdict(score).lower()}, text[:120])


class TestWithVerdict(unittest.TestCase):

    def test_bands(self):
        self.assertEqual([verdict(s) for s in ("9", "7", "6", "5", "4", "1", "NA", None)],
                         ["Strong fit", "Strong fit", "Moderate fit", "Moderate fit",
                          "Weak fit", "Weak fit", "Fit unclear", "Fit unclear"])
        self.assertEqual(VERDICTS, ("Strong fit", "Moderate fit", "Weak fit", "Fit unclear"))

    def test_missing_label_is_added(self):
        check(self, with_verdict("The company makes sequencing kits.", "8"), "8")

    def test_right_label_kept_once(self):
        t = with_verdict("Moderate fit: same condition, other approach.", "5")
        self.assertEqual(t, "Moderate fit: same condition, other approach.")

    def test_wrong_label_replaced(self):
        for wrong in ("Strong fit: overlap.", "strong fit - overlap.", "**Strong fit** overlap.",
                      "Strong Fit. overlap.", "Fit unclear: overlap."):
            t = with_verdict(wrong, "4")
            self.assertEqual(t, "Weak fit: overlap.", wrong)
            check(self, t, "4")

    def test_na_score(self):
        check(self, with_verdict("Weak fit: no description.", "NA"), "NA")

    def test_plain_justification(self):
        c = {"company": "Acme", "distance_miles": 12.0}
        for s in ("8", "5", "2"):
            t = plain_justification(c, {"score": s, "reason": "r"}, (0.5, "No funding record."))
            check(self, t, s)


class TestEveryTabRow(unittest.TestCase):
    """End to end: the AI labels its justifications wrongly on purpose and
    scores spread over 1-9; every row of every tab must still be right."""

    def test_all_tabs(self):
        install_fake_openai()
        orig = FakeOpenAI._Chat.create
        labels = ["Strong fit: ", "**Moderate fit** - ", "", "weak fit. ", "Fit unclear: "]

        def create(self, model, messages):
            system = messages[0]["content"].lower()
            user = messages[-1]["content"]
            company = next((l.split(":", 1)[1].strip() for l in user.splitlines()
                            if l.startswith("Company:")), "")
            h = int(hashlib.md5(company.encode()).hexdigest(), 16)
            if "justification" in system:
                FakeOpenAI.call_log.append("justification")
                return _ChatResp(json.dumps(
                    {"justification": labels[h % len(labels)] + f"Mock for {company}."}))
            if "how well companies align" in system and "unknown" not in company.lower() \
                    and "broken" not in company.lower():
                FakeOpenAI.call_log.append("alignment")
                return _ChatResp(json.dumps({"company": company, "score": 4 + h % 6,
                                             "reason": "r"}))
            return orig(self, model, messages)

        FakeOpenAI._Chat.create = create
        p = TempProject()
        p.__enter__()
        try:
            p.seed_geocodes()
            p.run_enrich()
            self.assertEqual(p.run_match(["--faculty", "Ioulia Valla"]), 0)
            wb = openpyxl.load_workbook(p.outputs()[0])
            seen, rows = set(), 0
            for ws in wb.worksheets:
                if not ws.title.startswith(("Small", "Mid", "Large", "Hospitals")):
                    continue
                hdr = [c.value for c in ws[1]]
                j, s = hdr.index("Justification"), hdr.index("Alignment Score (1-9)")
                for r in ws.iter_rows(min_row=2, values_only=True):
                    check(self, r[j], r[s])
                    seen.add(verdict(r[s]))
                    rows += 1
            self.assertGreater(rows, 0)
            self.assertGreaterEqual(len(seen), 2, seen)
        finally:
            FakeOpenAI._Chat.create = orig
            p.__exit__()


if __name__ == "__main__":
    unittest.main()
