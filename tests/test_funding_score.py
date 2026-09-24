"""Tests for the data-driven funding score.

This score replaced an LLM judgement, so the thing worth pinning down is
that it stays ARITHMETIC: same inputs, same answer, and the shape does what
the config says rather than what a model felt like that day.

The cases below are the ones that were argued over while designing it - the
non-monotonic size curve, absence-vs-zero, and max-vs-sum - because those
are the ones a well-meaning refactor would quietly get wrong.
"""
from __future__ import annotations

import math
import unittest

from nexus.scoring import funding_score, log_score, size_fit
from nexus.settings import Settings

CFG = Settings().funding
SIZE = CFG["size_fit"]
ACT = CFG["activity"]


class TestLogScore(unittest.TestCase):

    def test_ends_of_the_scale(self):
        self.assertEqual(log_score(100_000, 100_000, 100_000_000), 0.0)
        self.assertEqual(log_score(100_000_000, 100_000, 100_000_000), 1.0)

    def test_beyond_the_ends_is_clamped(self):
        self.assertEqual(log_score(1, 100_000, 100_000_000), 0.0)
        self.assertEqual(log_score(1e12, 100_000, 100_000_000), 1.0)

    def test_each_tenfold_step_is_evenly_spaced(self):
        """That is what makes it a log scale rather than a linear one."""
        lo, hi = 100_000, 100_000_000        # three decades
        self.assertAlmostEqual(log_score(1_000_000, lo, hi), 1 / 3, places=6)
        self.assertAlmostEqual(log_score(10_000_000, lo, hi), 2 / 3, places=6)

    def test_the_median_company_does_not_collapse(self):
        """Scored LINEARLY against the maximum, the median Form D raise of
        $5m reads 0.011 and the component stops discriminating. The whole
        reason for the log scale."""
        linear = 5_000_000 / 474_000_000
        self.assertLess(linear, 0.02)
        self.assertGreater(log_score(5_000_000, 100_000, 100_000_000), 0.5)

    def test_absence_is_none_not_zero(self):
        """None means 'no record', 0.0 means 'we know it is tiny'. The
        caller treats those differently, so they must not be conflated."""
        self.assertIsNone(log_score(None, 100_000, 100_000_000))

    def test_zero_and_negative_read_as_absent(self):
        self.assertIsNone(log_score(0, 100_000, 100_000_000))
        self.assertIsNone(log_score(-5, 100_000, 100_000_000))

    def test_nan_reads_as_absent(self):
        """pandas writes a blank cell as NaN, and NaN is TRUTHY in Python -
        the trap that once undercounted missing revenue twenty-fold."""
        self.assertIsNone(log_score(float("nan"), 100_000, 100_000_000))

    def test_junk_does_not_raise(self):
        self.assertIsNone(log_score("-", 100_000, 100_000_000))
        self.assertIsNone(log_score("", 100_000, 100_000_000))


class TestSizeFit(unittest.TestCase):

    def test_small_and_researching_scores_full(self):
        self.assertEqual(size_fit(2.0, True, SIZE), 1.0)

    def test_small_without_research_does_not(self):
        """A pre-revenue biotech and a corner shop have the same revenue.
        The research signal is the only thing separating them."""
        self.assertEqual(size_fit(2.0, False, SIZE), SIZE["small_no_research"])

    def test_the_middle_band_is_the_weakest(self):
        self.assertEqual(size_fit(40.0, True, SIZE), SIZE["dip"])
        self.assertEqual(size_fit(40.0, False, SIZE), SIZE["dip"])

    def test_established_but_not_enormous_scores_full(self):
        self.assertEqual(size_fit(150.0, False, SIZE), 1.0)
        self.assertEqual(size_fit(900.0, False, SIZE), 1.0)

    def test_the_giants_score_zero(self):
        """'Biggest company - no.' Pfizer has the money and no need of us."""
        self.assertEqual(size_fit(63_696.0, False, SIZE), 0.0)

    def test_the_curve_is_not_monotonic(self):
        """Bigger is NOT better. A refactor that sorts by revenue, or
        replaces this with a min/max normalisation, breaks the whole idea."""
        self.assertGreater(size_fit(150.0, False, SIZE),
                           size_fit(40.0, False, SIZE))
        self.assertGreater(size_fit(150.0, False, SIZE),
                           size_fit(63_696.0, False, SIZE))

    def test_taper_between_the_sweet_spot_and_zero(self):
        mid = (SIZE["sweet_hi"] + SIZE["taper_zero"]) / 2
        self.assertAlmostEqual(size_fit(mid, False, SIZE), 0.5, places=6)

    def test_nan_revenue_is_missing_not_zero(self):
        """pandas reads a blank cell as NaN, NaN is not None, and every
        comparison against NaN is False - so an `is None` check falls
        through the whole ladder and returns 0.0. That scored 15,275
        companies 0.30 instead of 0.50 on the first real run."""
        self.assertEqual(size_fit(float("nan"), True, SIZE), 1.0)
        self.assertEqual(size_fit(float("nan"), False, SIZE),
                         SIZE["missing_score"])

    def test_unparseable_revenue_is_missing(self):
        self.assertEqual(size_fit("-", False, SIZE), SIZE["missing_score"])

    def test_blank_revenue_defers_to_the_research_signal(self):
        """71% of the datasource has no revenue figure. Treating that as
        'small' or as zero would decide 15,000 companies on missing data."""
        self.assertEqual(size_fit(None, True, SIZE), 1.0)
        self.assertEqual(size_fit(None, False, SIZE), SIZE["missing_score"])


class TestFundingScore(unittest.TestCase):

    def test_nothing_known_is_neutral_not_zero(self):
        """The single most consequential rule. 98% of companies have no
        funding record; scoring them 0 would punish 21,304 companies for a
        gap in OUR data rather than a fact about them."""
        self.assertEqual(funding_score(None, None, None, CFG),
                         CFG["missing_score"])

    def test_a_heavily_funded_pre_revenue_biotech_tops_out(self):
        """Neumora: no revenue, $474m raised, $176m R&D."""
        self.assertEqual(funding_score(474.4e6, 176e6, 0.0, CFG), 1.0)

    def test_the_giants_rank_below_the_biotechs(self):
        """Pfizer outscoring a clinical-stage biotech would invert the
        entire point of the score."""
        pfizer = funding_score(None, None, 63_696.0, CFG)
        biotech = funding_score(200e6, 168e6, 2.0, CFG)
        self.assertLess(pfizer, biotech)

    def test_huge_rd_keeps_a_giant_in_contention(self):
        """Bristol-Myers spends $9.95bn on research. Too big to be a good
        target, but that figure is real - it should not read as zero."""
        plain = funding_score(None, None, 49_186.0, CFG)
        with_rd = funding_score(None, 9_951e6, 49_186.0, CFG)
        self.assertGreater(with_rd, plain)
        self.assertLess(with_rd, funding_score(200e6, 168e6, 2.0, CFG))

    def test_one_signal_is_not_penalised(self):
        """Combined with max(), not a mean. Big pharma files no Form D;
        a freshly funded biotech reports no R&D line. Averaging would halve
        both for the crime of being a normal company."""
        raised_only = funding_score(100e6, None, None, CFG)
        rd_only = funding_score(None, 1e9, None, CFG)
        both = funding_score(100e6, 1e9, None, CFG)
        self.assertEqual(raised_only, both)
        self.assertEqual(rd_only, both)

    def test_signals_do_not_add_up(self):
        """Two maxed signals must not beat one maxed signal."""
        self.assertLessEqual(funding_score(1e9, 1e10, 150.0, CFG), 1.0)

    def test_zero_funding_is_treated_as_absent(self):
        self.assertEqual(funding_score(0, 0, None, CFG),
                         funding_score(None, None, None, CFG))

    def test_score_is_always_in_range(self):
        for f in (None, 0, 1e3, 1e6, 1e9, 1e12):
            for r in (None, 0, 1e6, 1e12):
                for rev in (None, 0.0, 3.0, 40.0, 150.0, 2000.0, 999_999.0):
                    s = funding_score(f, r, rev, CFG)
                    self.assertGreaterEqual(s, 0.0)
                    self.assertLessEqual(s, 1.0)

    def test_it_is_deterministic(self):
        """It replaced an LLM call. Same inputs, same answer, every time."""
        args = (12_345_678, 9_876_543, 42.0)
        self.assertEqual(funding_score(*args, CFG), funding_score(*args, CFG))


if __name__ == "__main__":
    unittest.main()
