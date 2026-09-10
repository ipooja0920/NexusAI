"""Tests for nexus/funding.py - joining the source caches onto companies.

This is a lookup, not a computation: the source steps already did the
matching. What matters here is that a missing cache degrades to blank
columns rather than an error, that the two sources combine the way the
spec says, and that lifetime totals never leak into the "recent" figure.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nexus.funding import (FUNDING_COLUMNS, funding_columns, load_caches,
                           summarise)

FORMD = {
    "acme therapeutics": {
        "matched_entity": "Acme Therapeutics, Inc.",
        "last_raise_usd": 8_000_000.0,
        "total_raised_usd": 20_000_000.0,
        "by_year": "2025:$8.00M; 2023:$12.00M",
    },
    "old money": {
        "matched_entity": "Old Money LLC",
        "last_raise_usd": None,
        "total_raised_usd": None,
        "by_year": "",
    },
}

SBIR = {
    "acme therapeutics": {
        "sbir_matched_name": "ACME THERAPEUTICS INC",
        "sbir_recent_usd": 2_000_000.0,
        "sbir_total_usd": 9_000_000.0,
        "sbir_by_year": "2024:$2.00M; 2016:$7.00M",
        "sbir_awards": 6, "sbir_first_year": 2014, "sbir_last_year": 2024,
        "sbir_phase2_awards": 2,
        "sbir_agencies": "Department of Health and Human Services (6)",
        "sbir_agency_industries": "Life Sciences; Health Care",
        "university_past": "Boston University; Yale University",
        "university_current": "Yale University",
        "sbir_employees": 42,
    },
    "dormant labs": {
        "sbir_matched_name": "DORMANT LABS",
        "sbir_recent_usd": 0,               # nothing lately
        "sbir_total_usd": 38_000_000.0,     # a lot, long ago
        "sbir_by_year": "2005:$3.00M; 2004:$4.00M",
        "sbir_awards": 40, "sbir_first_year": 1997, "sbir_last_year": 2005,
        "sbir_phase2_awards": 9,
        "university_past": "", "university_current": "",
        "sbir_employees": None,
    },
}


XBRL = {
    "acme therapeutics": {
        "ticker": "ACME", "cik": 1234567,
        "rd_expense_usd": 99_000_000.0,
        "rd_expense_year": 2025,
        "rd_by_year": "2025:$99.00M; 2024:$88.00M",
    },
}


def _cache_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="nexusai_funding_"))
    (d / "funding_formd.json").write_text(json.dumps(FORMD))
    (d / "funding_sbir.json").write_text(json.dumps(SBIR))
    (d / "rd_xbrl.json").write_text(json.dumps(XBRL))
    return d


class TestLoadCaches(unittest.TestCase):

    def test_reads_all(self):
        c = load_caches(_cache_dir())
        self.assertEqual(set(c), {"formd", "sbir", "xbrl"})

    def test_missing_directory_is_not_an_error(self):
        """A source step that has never run must not break enrichment."""
        self.assertEqual(load_caches(Path("/nonexistent/path")), {})

    def test_one_cache_present(self):
        d = _cache_dir()
        (d / "funding_sbir.json").unlink()
        (d / "rd_xbrl.json").unlink()
        self.assertEqual(set(load_caches(d)), {"formd"})

    def test_corrupt_cache_is_skipped_not_fatal(self):
        d = _cache_dir()
        (d / "funding_sbir.json").write_text("{not json")
        self.assertEqual(set(load_caches(d)), {"formd", "xbrl"})


class TestFundingColumns(unittest.TestCase):

    def setUp(self):
        self.caches = load_caches(_cache_dir())

    def test_company_in_both_sources(self):
        c = funding_columns("acme therapeutics", self.caches)
        self.assertEqual(c["formd_recent_usd"], 8_000_000.0)
        self.assertEqual(c["sbir_recent_usd"], 2_000_000.0)
        self.assertEqual(c["funding_recent_usd"], 10_000_000.0)
        self.assertEqual(c["funding_total_usd"], 29_000_000.0)
        self.assertEqual(c["funding_sources"], "Form D; SBIR")

    def test_recent_never_borrows_from_lifetime(self):
        """Dormant Labs raised $38m, all of it before 2006. It must read as
        having no current capacity while keeping the history visible."""
        c = funding_columns("dormant labs", self.caches)
        self.assertIsNone(c["funding_recent_usd"])
        self.assertEqual(c["sbir_total_usd"], 38_000_000.0)
        self.assertIn("2005", c["sbir_by_year"])

    def test_company_in_neither_source(self):
        c = funding_columns("nobody at all", self.caches)
        self.assertIsNone(c["funding_sources"])
        self.assertIsNone(c["funding_recent_usd"])
        self.assertIsNone(c["university_past"])

    def test_every_declared_column_is_produced(self):
        """The workbook layout is built from FUNDING_COLUMNS, so a column
        declared but never populated would appear as a silent blank."""
        c = funding_columns("acme therapeutics", self.caches)
        self.assertEqual(set(c), set(FUNDING_COLUMNS))

    def test_columns_are_stable_when_no_match(self):
        a = set(funding_columns("acme therapeutics", self.caches))
        b = set(funding_columns("nobody at all", self.caches))
        self.assertEqual(a, b)

    def test_university_columns_are_source_neutral(self):
        """Named without a prefix because OpenAlex will feed the same two."""
        c = funding_columns("acme therapeutics", self.caches)
        self.assertEqual(c["university_current"], "Yale University")
        self.assertEqual(c["university_partners"], 2)
        self.assertIn("Boston University", c["university_past"])

    def test_zero_is_not_reported_as_a_figure(self):
        c = funding_columns("dormant labs", self.caches)
        self.assertIsNone(c["sbir_recent_usd"])

    def test_rd_expense_is_not_counted_as_funding(self):
        """R&D spend is money going OUT. It must never inflate the funding
        figures, which represent money coming in."""
        c = funding_columns("acme therapeutics", self.caches)
        self.assertEqual(c["rd_expense_usd"], 99_000_000.0)
        self.assertEqual(c["funding_recent_usd"], 10_000_000.0)   # unchanged
        self.assertEqual(c["funding_total_usd"], 29_000_000.0)    # unchanged
        self.assertNotIn("XBRL", c["funding_sources"])

    def test_rd_columns_blank_for_a_private_company(self):
        c = funding_columns("dormant labs", self.caches)
        self.assertIsNone(c["rd_expense_usd"])
        self.assertIsNone(c["xbrl_ticker"])

    def test_provenance_is_carried(self):
        c = funding_columns("acme therapeutics", self.caches)
        self.assertEqual(c["formd_match"], "Acme Therapeutics, Inc.")
        self.assertEqual(c["sbir_match"], "ACME THERAPEUTICS INC")

    def test_no_caches_at_all(self):
        c = funding_columns("acme therapeutics", {})
        self.assertEqual(set(c), set(FUNDING_COLUMNS))
        self.assertIsNone(c["funding_sources"])


class TestSummarise(unittest.TestCase):

    def test_nan_revenue_counts_as_missing(self):
        """pandas writes a blank cell as NaN, and NaN is TRUTHY in Python -
        so `not value` reports every blank as populated. That bug undercounted
        companies without revenue by more than twenty times."""
        caches = load_caches(_cache_dir())
        records = []
        for rev in (float("nan"), None, 0, 0.0, 55.0):
            r = {"revenue_usdmm": rev}
            r.update(funding_columns("acme therapeutics", caches))
            records.append(r)
        self.assertEqual(summarise(records)["no Capital IQ revenue"], 4)

    def test_counts_companies_lit_up(self):
        caches = load_caches(_cache_dir())
        records = []
        for key, rev in (("acme therapeutics", None), ("dormant labs", None),
                         ("nobody at all", 55.0)):
            r = {"revenue_usdmm": rev}
            r.update(funding_columns(key, caches))
            records.append(r)
        s = summarise(records)
        self.assertEqual(s["companies"], 3)
        self.assertEqual(s["with funding from any source"], 2)
        self.assertEqual(s["with recent funding"], 1)   # dormant has none
        self.assertEqual(s["no Capital IQ revenue"], 2)
        self.assertEqual(s["  of those, now have funding data"], 2)


if __name__ == "__main__":
    unittest.main()


class TestTickerParsing(unittest.TestCase):
    """Capital IQ's Exchange:Ticker column is the whole basis of the XBRL
    join, so its parsing is worth pinning down."""

    def test_exchange_prefix_stripped(self):
        from enrich_xbrl import ticker_of
        self.assertEqual(ticker_of("NasdaqCM:ACET"), "ACET")
        self.assertEqual(ticker_of("OTCPK:ABPO"), "ABPO")
        self.assertEqual(ticker_of("NYSE:XYZ"), "XYZ")

    def test_bare_symbol(self):
        from enrich_xbrl import ticker_of
        self.assertEqual(ticker_of("acet"), "ACET")

    def test_private_companies_have_none(self):
        from enrich_xbrl import ticker_of
        for blank in ("-", "", "   ", None, "nan"):
            self.assertIsNone(ticker_of(blank))
