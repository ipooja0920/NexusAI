"""Tests for the rest of the XBRL pipeline, around clean_xbrl.py:

  fetch_sec_xbrl.py    which periods are asked for, and when a saved
                       download is reused or fetched again (no network -
                       the download function is replaced)
  combine_companies.py how a public company is found (ticker, then Form D
                       CIK), which figures are shown, the cash total, and
                       the blank-revenue fill

Made-up data only.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

import clean_xbrl
import fetch_sec_xbrl as F
from combine_companies import (fill_revenue, load_ticker_ciks, match_xbrl,
                               summarise_xbrl, ticker_of)


# ----------------------------------------------------------------------
# fetch_sec_xbrl.py
# ----------------------------------------------------------------------

class TestPeriods(unittest.TestCase):

    def test_last_four_quarter_ends_before_today(self):
        self.assertEqual(F.default_quarters(date(2026, 9, 30)),
                         [(2025, 3), (2025, 4), (2026, 1), (2026, 2)])

    def test_quarter_ending_today_not_yet_included(self):
        """Nobody has filed for a quarter that ends today."""
        self.assertNotIn((2026, 3), F.default_quarters(date(2026, 9, 30)))
        self.assertIn((2026, 3), F.default_quarters(date(2026, 10, 1)))

    def test_wraps_over_new_year(self):
        self.assertEqual(F.default_quarters(date(2026, 1, 15)),
                         [(2025, 1), (2025, 2), (2025, 3), (2025, 4)])

    def test_parse_quarter(self):
        self.assertEqual(F.parse_quarter("2026Q2"), (2026, 2))
        self.assertEqual(F.parse_quarter("2026q1"), (2026, 1))
        for bad in ("2026Q5", "2026", "Q2", "26-Q2"):
            with self.assertRaises(Exception):
                F.parse_quarter(bad)

    def test_flow_tags_asked_by_year_stock_tags_by_quarter_end(self):
        self.assertEqual([p for p, _, _ in F.periods_for("Revenues", [2024, 2025], [(2026, 2)])],
                         ["CY2024", "CY2025"])
        self.assertEqual([p for p, _, _ in F.periods_for(
            "CashAndCashEquivalentsAtCarryingValue", [2025], [(2025, 4), (2026, 2)])],
            ["CY2025Q4I", "CY2026Q2I"])

    def test_every_fetched_tag_is_read_by_clean(self):
        """A tag fetched but not listed in clean_xbrl would be downloaded and
        then silently ignored."""
        for tag in F.DEFAULT_TAGS:
            self.assertIn(tag, clean_xbrl.TAG_TO_CONCEPT, tag)
            self.assertIn(tag, F.TAG_SLUG, tag)
        self.assertEqual(len(set(F.TAG_SLUG.values())), len(F.TAG_SLUG),
                         "two tags share a file name")

    def test_every_tag_read_by_clean_is_fetched(self):
        self.assertEqual(set(clean_xbrl.TAG_TO_CONCEPT), set(F.DEFAULT_TAGS))

    def test_stock_tags_are_all_balance_sheet_concepts(self):
        for tag in F.INSTANT_TAGS:
            self.assertIn(clean_xbrl.TAG_TO_CONCEPT[tag],
                          ("cash", "short_term_investments",
                           "cash_and_short_term_investments"))


class TestCache(unittest.TestCase):
    """load_or_fetch: reuse a saved download only once its period is final."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nexusai_fetch_"))
        self.path = self.dir / "frames_cash_CY2026Q2I.json"
        self.calls = []
        self._orig = F._get_json
        F._get_json = lambda url: (self.calls.append(url) or {"new": True})
        F.PAUSE, self._pause = 0, F.PAUSE

    def tearDown(self):
        F._get_json = self._orig
        F.PAUSE = self._pause

    def save(self, saved_on: date):
        self.path.write_text(json.dumps({"old": True}))
        t = time.mktime(saved_on.timetuple())
        os.utime(self.path, (t, t))

    def test_saved_after_final_is_reused(self):
        self.save(date(2026, 11, 1))
        got = F.load_or_fetch(self.path, "u", False, date(2026, 10, 28))
        self.assertEqual(got, {"old": True})
        self.assertEqual(self.calls, [])

    def test_saved_before_final_is_fetched_again(self):
        """A frame saved a few weeks after quarter-end is missing late filers."""
        self.save(date(2026, 8, 1))
        got = F.load_or_fetch(self.path, "u", False, date(2026, 10, 28))
        self.assertEqual(got, {"new": True})
        self.assertEqual(json.loads(self.path.read_text()), {"new": True})

    def test_refresh_always_fetches(self):
        self.save(date(2026, 11, 1))
        F.load_or_fetch(self.path, "u", True, date(2026, 10, 28))
        self.assertEqual(self.calls, ["u"])

    def test_not_published_writes_nothing(self):
        F._get_json = lambda url: None
        self.assertIsNone(F.load_or_fetch(self.path, "u", False, None))
        self.assertFalse(self.path.exists())


# ----------------------------------------------------------------------
# combine_companies.py - finding the company
# ----------------------------------------------------------------------

class TestMatch(unittest.TestCase):

    XBRL = {100: {}, 200: {}}
    TICKERS = {"ACET": 100, "NOPE": 999}

    def test_ticker_of(self):
        self.assertEqual(ticker_of("NasdaqCM:ACET"), "ACET")
        self.assertEqual(ticker_of("OTCPK:SEEL.Q"), "SEEL.Q")
        self.assertEqual(ticker_of(" acet "), "ACET")
        for blank in ("-", "", None, float("nan"), "nan"):
            self.assertIsNone(ticker_of(blank))

    def test_ticker_first(self):
        self.assertEqual(match_xbrl("NasdaqCM:ACET", "200", self.XBRL, self.TICKERS),
                         (100, "ticker"))

    def test_formd_cik_when_no_ticker(self):
        self.assertEqual(match_xbrl("-", "200", self.XBRL, self.TICKERS),
                         (200, "formd_cik"))

    def test_formd_cik_when_ticker_unknown_to_sec(self):
        """Curaleaf trades in Toronto - SEC's list has no such ticker."""
        self.assertEqual(match_xbrl("TSX:CURA", "200", self.XBRL, self.TICKERS),
                         (200, "formd_cik"))

    def test_formd_cik_when_ticker_company_has_no_figures(self):
        self.assertEqual(match_xbrl("NYSE:NOPE", "200", self.XBRL, self.TICKERS),
                         (200, "formd_cik"))

    def test_nothing(self):
        self.assertEqual(match_xbrl("-", None, self.XBRL, self.TICKERS), (None, None))
        self.assertEqual(match_xbrl("-", "555", self.XBRL, self.TICKERS), (None, None))
        self.assertEqual(match_xbrl("-", "not a number", self.XBRL, self.TICKERS),
                         (None, None))

    def test_ticker_map_includes_every_share_class(self):
        d = Path(tempfile.mkdtemp()) / "t.json"
        d.write_text(json.dumps({"0": {"cik_str": 1, "ticker": "GOOGL", "title": "A"},
                                 "1": {"cik_str": 1, "ticker": "GOOG", "title": "A"}}))
        self.assertEqual(load_ticker_ciks(d), {"GOOGL": 1, "GOOG": 1})
        self.assertEqual(load_ticker_ciks(d.parent / "missing.json"), {})


# ----------------------------------------------------------------------
# combine_companies.py - which figures are shown
# ----------------------------------------------------------------------

def summary(*rows):
    """rows: (cik, concept, period, period_end, value_usd)."""
    f = Path(tempfile.mkdtemp()) / "x.csv"
    pd.DataFrame([dict(company=f"Co{r[0]}", cik=r[0], concept=r[1], period=r[2],
                       period_type="instant" if r[2].endswith("I") else "year",
                       period_end=r[3], value_usd=r[4]) for r in rows]).to_csv(f, index=False)
    return summarise_xbrl(f)


class TestSummary(unittest.TestCase):

    def test_latest_year_for_rd_and_revenue(self):
        s = summary((1, "rd_expense", "CY2024", "2024-12-31", 4e6),
                    (1, "rd_expense", "CY2025", "2025-12-31", 5e6),
                    (1, "revenue", "CY2024", "2024-12-31", 9e6))[1]
        self.assertEqual((s["xbrl_rd_expense_usdmm"], s["xbrl_rd_year"]), (5.0, 2025))
        self.assertEqual((s["xbrl_revenue_usdmm"], s["xbrl_revenue_year"]), (9.0, 2024))

    def test_real_zero_revenue_is_the_latest(self):
        """Amylyx: $87m in 2024, a real $0 in 2025 - show 2025's $0."""
        s = summary((1, "revenue", "CY2024", "2024-12-31", 87e6),
                    (1, "revenue", "CY2025", "2025-12-31", 0))[1]
        self.assertEqual((s["xbrl_revenue_usdmm"], s["xbrl_revenue_year"]), (0.0, 2025))

    def test_latest_balance_sheet_not_quarters_added(self):
        s = summary((1, "cash", "CY2026Q1I", "2026-03-31", 10e6),
                    (1, "cash", "CY2026Q2I", "2026-06-30", 8e6))[1]
        self.assertEqual((s["xbrl_cash_usdmm"], s["xbrl_cash_as_of"]), (8.0, "2026-06-30"))

    def test_cash_and_investments_from_the_same_day(self):
        """Never a June cash figure with a March investments figure."""
        s = summary((1, "cash", "CY2026Q1I", "2026-03-31", 10e6),
                    (1, "short_term_investments", "CY2026Q1I", "2026-03-31", 40e6),
                    (1, "cash", "CY2026Q2I", "2026-06-30", 8e6))[1]
        self.assertEqual(s["xbrl_cash_usdmm"], 8.0)
        self.assertIsNone(s["xbrl_short_term_investments_usdmm"])
        self.assertEqual(s["xbrl_cash_total_usdmm"], 8.0)

    def test_total_is_cash_plus_investments(self):
        """Corbus, 30 June 2026."""
        s = summary((1, "cash", "CY2026Q2I", "2026-06-30", 23_370_000),
                    (1, "short_term_investments", "CY2026Q2I", "2026-06-30", 94_571_000))[1]
        self.assertEqual(s["xbrl_cash_total_usdmm"], 117.941)
        self.assertEqual(s["xbrl_cash_total_from"], "cash + short-term")

    def test_company_total_used_when_clearly_larger(self):
        """Aktis: $82.5m cash, but its own total of $517.3m."""
        s = summary((1, "cash", "CY2026Q2I", "2026-06-30", 82.5e6),
                    (1, "cash_and_short_term_investments", "CY2026Q2I", "2026-06-30", 517.3e6))[1]
        self.assertEqual(s["xbrl_cash_total_usdmm"], 517.3)
        self.assertEqual(s["xbrl_cash_usdmm"], 82.5)
        self.assertEqual(s["xbrl_cash_total_from"], "company's own total")

    def test_company_total_ignored_when_only_rounding(self):
        """Acumen: its $110.2m against our exact $110.197m."""
        s = summary((1, "cash", "CY2026Q2I", "2026-06-30", 59_443_000),
                    (1, "short_term_investments", "CY2026Q2I", "2026-06-30", 50_754_000),
                    (1, "cash_and_short_term_investments", "CY2026Q2I", "2026-06-30", 110_200_000))[1]
        self.assertEqual(s["xbrl_cash_total_usdmm"], 110.197)
        self.assertEqual(s["xbrl_cash_total_from"], "cash + short-term")

    def test_company_total_smaller_is_ignored(self):
        s = summary((1, "cash", "CY2026Q2I", "2026-06-30", 50e6),
                    (1, "cash_and_short_term_investments", "CY2026Q2I", "2026-06-30", 40e6))[1]
        self.assertEqual(s["xbrl_cash_total_usdmm"], 50.0)

    def test_company_total_alone(self):
        s = summary((1, "cash", "CY2026Q1I", "2026-03-31", 4e6),
                    (1, "cash_and_short_term_investments", "CY2026Q2I", "2026-06-30", 9e6))[1]
        self.assertIsNone(s["xbrl_cash_usdmm"])
        self.assertEqual((s["xbrl_cash_total_usdmm"], s["xbrl_cash_as_of"]), (9.0, "2026-06-30"))
        self.assertEqual(s["xbrl_cash_total_from"], "company's own total")

    def test_total_added_before_rounding(self):
        s = summary((1, "cash", "CY2026Q2I", "2026-06-30", 1_000_400),
                    (1, "short_term_investments", "CY2026Q2I", "2026-06-30", 1_000_400))[1]
        self.assertEqual(s["xbrl_cash_total_usdmm"], 2.001)   # not 1.0 + 1.0

    def test_company_with_no_balance_sheet(self):
        s = summary((1, "rd_expense", "CY2025", "2025-12-31", 5e6))[1]
        self.assertNotIn("xbrl_cash_as_of", s)


# ----------------------------------------------------------------------
# combine_companies.py - blank Capital IQ revenue
# ----------------------------------------------------------------------

class TestRevenueFill(unittest.TestCase):

    def test_capital_iq_figure_never_replaced(self):
        out, src = fill_revenue(["12.5"], [99.0], [2025])
        self.assertEqual((out, src), (["12.5"], ["Capital IQ"]))

    def test_capital_iq_zero_is_a_figure(self):
        out, src = fill_revenue(["0"], [70.0], [2025])
        self.assertEqual((out, src), (["0"], ["Capital IQ"]))

    def test_blank_filled_however_old(self):
        for blank in ("-", "", None, float("nan")):
            out, src = fill_revenue([blank], [223.378], [2023])
            self.assertEqual((out, src), (["223.378"], ["SEC XBRL (2023)"]))

    def test_blank_with_nothing_from_sec_stays_blank(self):
        out, src = fill_revenue(["-"], [None], [None])
        self.assertEqual((out, src), (["-"], [None]))

    def test_sec_zero_fills(self):
        out, src = fill_revenue(["-"], [0.0], [2025])
        self.assertEqual(src, ["SEC XBRL (2025)"])


if __name__ == "__main__":
    unittest.main()
