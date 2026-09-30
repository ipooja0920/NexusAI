"""Tests for clean_xbrl.py - SEC XBRL frames to one row per company,
concept and period.

Each test writes a few made-up frame files into a temp folder, laid out
exactly like SEC's frames API responses, and cleans them. No real data, no
network.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from clean_xbrl import OUT_COLUMNS, clean, period_type


def frame(tag, ccp, rows, uom="USD"):
    """rows: (cik, name, value) or (cik, name, value, end)."""
    data = []
    for r in rows:
        cik, name, val = r[:3]
        end = r[3] if len(r) > 3 else "2025-12-31"
        d = {"accn": f"acc-{cik}-{ccp}", "cik": cik, "entityName": name,
             "loc": "US-CT", "end": end, "val": val}
        if not ccp.endswith("I"):
            d["start"] = "2025-01-01"
        data.append(d)
    return {"taxonomy": "us-gaap", "tag": tag, "ccp": ccp, "uom": uom,
            "pts": len(data), "data": data}


def run(files: dict, tickers: dict | None = None):
    """files: filename -> frame dict. Every file is saved as frames_<name>,
    the only files clean_xbrl.py reads."""
    root = Path(tempfile.mkdtemp(prefix="nexusai_xbrl_"))
    for name, f in files.items():
        name = name if name.startswith("frames_") else f"frames_{name}"
        (root / name).write_text(json.dumps(f), encoding="utf-8")
    if tickers is not None:
        (root / "company_tickers.json").write_text(json.dumps(
            {str(i): {"cik_str": c, "ticker": t, "title": "x"}
             for i, (c, t) in enumerate(tickers)}), encoding="utf-8")
    return clean(root)


def one(df, cik, concept, period):
    m = df[(df.cik == cik) & (df.concept == concept) & (df.period == period)]
    assert len(m) == 1, f"{len(m)} rows for {cik} {concept} {period}"
    return m.iloc[0]


R_AND_D = "ResearchAndDevelopmentExpense"
RD_EXCL = "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"
ASC606 = "RevenueFromContractWithCustomerExcludingAssessedTax"
LEGACY = "Revenues"
CASH = "CashAndCashEquivalentsAtCarryingValue"
CASH_R = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
STI = "ShortTermInvestments"
MSC = "MarketableSecuritiesCurrent"


class TestShape(unittest.TestCase):

    def test_columns_and_one_row_per_fact(self):
        df = run({"frames_rd_expense_CY2025.json":
                  frame(R_AND_D, "CY2025", [(1, "Acme", 5e6), (2, "Beta", 7e6)])})
        self.assertEqual(list(df.columns), OUT_COLUMNS)
        self.assertEqual(len(df), 2)
        r = one(df, 1, "rd_expense", "CY2025")
        self.assertEqual(r.value_usd, 5e6)
        self.assertEqual(r.tag, R_AND_D)
        self.assertEqual(r.period_type, "year")

    def test_every_year_kept_not_just_latest(self):
        df = run({"a.json": frame(R_AND_D, "CY2024", [(1, "Acme", 4e6)]),
                  "b.json": frame(R_AND_D, "CY2025", [(1, "Acme", 5e6)])})
        self.assertEqual(sorted(df.period), ["CY2024", "CY2025"])

    def test_no_frames_is_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            run({})


class TestTagPriority(unittest.TestCase):

    def test_first_tag_wins_when_company_reports_both(self):
        """Tags are alternatives for the same number: never summed."""
        df = run({"a.json": frame(ASC606, "CY2025", [(1, "Acme", 100)]),
                  "b.json": frame(LEGACY, "CY2025", [(1, "Acme", 120)])})
        r = one(df, 1, "revenue", "CY2025")
        self.assertEqual(r.value_usd, 100)
        self.assertEqual(r.tag, ASC606)

    def test_fallback_tag_used_when_first_missing(self):
        """Organogenesis reports only under 'Revenues'."""
        df = run({"a.json": frame(ASC606, "CY2025", [(1, "Kymera", 39)]),
                  "b.json": frame(LEGACY, "CY2025", [(2, "Organogenesis", 564)])})
        self.assertEqual(one(df, 2, "revenue", "CY2025").tag, LEGACY)
        self.assertEqual(one(df, 1, "revenue", "CY2025").tag, ASC606)

    def test_priority_resolved_per_period(self):
        """A company that switched tags keeps both years."""
        df = run({"a.json": frame(LEGACY, "CY2024", [(1, "Acme", 90)]),
                  "b.json": frame(ASC606, "CY2025", [(1, "Acme", 100)])})
        self.assertEqual(one(df, 1, "revenue", "CY2024").tag, LEGACY)
        self.assertEqual(one(df, 1, "revenue", "CY2025").tag, ASC606)

    def test_zero_on_first_tag_falls_through_to_nonzero(self):
        df = run({"a.json": frame(ASC606, "CY2025", [(1, "Acme", 0)]),
                  "b.json": frame(LEGACY, "CY2025", [(1, "Acme", 120)])})
        r = one(df, 1, "revenue", "CY2025")
        self.assertEqual((r.value_usd, r.tag), (120, LEGACY))

    def test_zero_on_every_tag_stays_zero(self):
        """Amylyx: $87m revenue in 2024, a real $0 in 2025."""
        df = run({"a.json": frame(ASC606, "CY2025", [(1, "Amylyx", 0)]),
                  "b.json": frame(LEGACY, "CY2025", [(1, "Amylyx", 0)])})
        r = one(df, 1, "revenue", "CY2025")
        self.assertEqual((r.value_usd, r.tag), (0, ASC606))

    def test_rd_fallback_tag(self):
        df = run({"a.json": frame(RD_EXCL, "CY2025", [(1, "Acme", 9e6)])})
        self.assertEqual(one(df, 1, "rd_expense", "CY2025").value_usd, 9e6)

    def test_cash_plain_before_restricted(self):
        df = run({"a.json": frame(CASH_R, "CY2026Q2I", [(1, "Acme", 55)], ),
                  "b.json": frame(CASH, "CY2026Q2I", [(1, "Acme", 50)])})
        r = one(df, 1, "cash", "CY2026Q2I")
        self.assertEqual((r.value_usd, r.tag), (50, CASH))

    def test_short_term_investment_tags_not_summed(self):
        df = run({"a.json": frame(MSC, "CY2026Q2I", [(1, "Acme", 30)]),
                  "b.json": frame(STI, "CY2026Q2I", [(1, "Acme", 30)])})
        r = one(df, 1, "short_term_investments", "CY2026Q2I")
        self.assertEqual((r.value_usd, r.tag), (30, STI))

    def test_less_common_investment_label_used_when_others_missing(self):
        htm = "DebtSecuritiesHeldToMaturityAmortizedCostAfterAllowanceForCreditLossCurrent"
        df = run({"a.json": frame(htm, "CY2026Q2I", [(1, "Acme", 70)])})
        r = one(df, 1, "short_term_investments", "CY2026Q2I")
        self.assertEqual((r.value_usd, r.tag), (70, htm))

    def test_company_own_total_kept_as_its_own_concept(self):
        """The reported total is a check on our sum, never a part of it."""
        df = run({"a.json": frame(CASH, "CY2026Q2I", [(1, "Acme", 50)]),
                  "b.json": frame("CashCashEquivalentsAndShortTermInvestments",
                                  "CY2026Q2I", [(1, "Acme", 300)])})
        self.assertEqual(one(df, 1, "cash", "CY2026Q2I").value_usd, 50)
        self.assertEqual(one(df, 1, "cash_and_short_term_investments",
                             "CY2026Q2I").value_usd, 300)

    def test_cash_and_investments_are_separate_concepts(self):
        df = run({"a.json": frame(CASH, "CY2026Q2I", [(1, "Acme", 50)]),
                  "b.json": frame(STI, "CY2026Q2I", [(1, "Acme", 30)])})
        self.assertEqual(sorted(df.concept), ["cash", "short_term_investments"])


class TestFiles(unittest.TestCase):

    def test_identified_by_contents_not_filename(self):
        """A renamed revenue file once silently dropped all revenue."""
        df = run({"frames_whatever_name.json": frame(ASC606, "CY2025", [(1, "Acme", 100)])})
        self.assertEqual(one(df, 1, "revenue", "CY2025").period, "CY2025")

    def test_duplicate_file_for_same_tag_and_period_counted_once(self):
        f = frame(R_AND_D, "CY2025", [(1, "Acme", 5e6)])
        df = run({"frames_a.json": f, "frames_b.json": f})
        self.assertEqual(len(df), 1)

    def test_non_usd_frame_ignored(self):
        df = run({"a.json": frame(R_AND_D, "CY2025", [(1, "Acme", 5e6)]),
                  "b.json": frame(CASH, "CY2026Q2I", [(1, "Acme", 9)], uom="EUR")})
        self.assertEqual(list(df.concept), ["rd_expense"])

    def test_unused_tag_ignored(self):
        df = run({"a.json": frame(R_AND_D, "CY2025", [(1, "Acme", 5e6)]),
                  "b.json": frame("NetIncomeLoss", "CY2025", [(1, "Acme", 1)])})
        self.assertEqual(len(df), 1)

    def test_other_json_files_not_read(self):
        """company_tickers.json sits in the same folder."""
        df = run({"frames_a.json": frame(R_AND_D, "CY2025", [(1, "Acme", 5e6)])},
                 tickers=[(1, "ACME")])
        self.assertEqual(len(df), 1)


class TestColumns(unittest.TestCase):

    def test_period_type(self):
        self.assertEqual(period_type("CY2025"), "year")
        self.assertEqual(period_type("CY2026Q2I"), "instant")
        self.assertEqual(period_type("CY2025Q3"), "quarter")

    def test_instant_row_has_end_but_no_start(self):
        df = run({"a.json": frame(CASH, "CY2026Q2I", [(1, "Acme", 50, "2026-06-30")])})
        r = one(df, 1, "cash", "CY2026Q2I")
        self.assertEqual(r.period_type, "instant")
        self.assertEqual(r.period_end, "2026-06-30")
        self.assertTrue(pd.isna(r.period_start))

    def test_common_stock_ticker_preferred(self):
        """Alphabet has GOOGL, GOOG, GOOGN - GOOG, not a preferred share."""
        df = run({"a.json": frame(R_AND_D, "CY2025", [(1, "Alphabet", 5e6)])},
                 tickers=[(1, "GOOGN"), (1, "GOOG"), (1, "GOOGL"), (1, "GOOG-P")])
        self.assertEqual(one(df, 1, "rd_expense", "CY2025").ticker, "GOOG")

    def test_zero_cash_kept(self):
        """Zero is a real balance, not a missing one."""
        df = run({"a.json": frame(CASH, "CY2026Q2I", [(1, "Acme", 0)])})
        self.assertEqual(one(df, 1, "cash", "CY2026Q2I").value_usd, 0)


if __name__ == "__main__":
    unittest.main()
