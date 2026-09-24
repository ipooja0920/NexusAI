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

from nexus.scoring import (funding_score, log_score, recency_factor,
                           size_fit, university_adjustment)
from nexus.settings import Settings

CFG = Settings().funding
SIZE = CFG["size_fit"]
ACT = CFG["activity"]
REC = CFG["recency"]
UNI = CFG["university"]


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
        """Neumora: no revenue, $474m raised, $176m R&D, reported this year."""
        self.assertEqual(
            funding_score(474.4e6, 176e6, 0.0, CFG, years=[REC["fresh_year"]]),
            1.0)

    def test_the_same_figures_without_a_date_do_not_top_out(self):
        """An undated figure is a weaker claim than a dated one, so it must
        not score the same. It still beats knowing nothing."""
        undated = funding_score(474.4e6, 176e6, 0.0, CFG)
        self.assertLess(undated, 1.0)
        self.assertGreater(undated, CFG["missing_score"])

    def test_stale_evidence_still_beats_no_evidence(self):
        """The rule that broke first: multiplying towards zero made
        Bristol-Myers, with $9.95bn of undated R&D, score BELOW an identical
        company with no R&D data at all. Evidence must never leave a company
        worse off than having none."""
        blind = funding_score(None, None, 49_186.0, CFG)
        stale = funding_score(None, 9_951e6, 49_186.0, CFG, years=[2019])
        self.assertGreater(stale, blind)

    def test_a_tiny_raise_is_not_laundered_up_to_neutral(self):
        """Shrinking towards neutral must not erase what we DO know. A
        company whose only raise was $100k still reads as tiny."""
        tiny = funding_score(100_000, None, 40.0, CFG,
                             years=[REC["fresh_year"]])
        self.assertLess(tiny, CFG["missing_score"])

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


class TestSizeAloneDoesNotEarnAFundingScore(unittest.TestCase):
    """Without a funding or R&D record, size_fit is the only term left -
    and size_fit is just revenue. Unchecked, that turned this column into a
    second revenue score and let a company be promoted for being large."""

    def test_a_large_company_with_no_research_is_only_neutral(self):
        """A regional insurer with $450m of premiums read 0.70 for 'can
        they fund research' and outranked a $38m catalysis firm."""
        insurer = funding_score(None, None, 450.0, CFG)
        self.assertEqual(insurer, CFG["missing_score"])

    def test_the_giants_keep_their_penalty(self):
        """The cap is one-directional. Size can still show a company is a
        POOR target; it just cannot show one is a good target."""
        self.assertLess(funding_score(None, None, 63_696.0, CFG),
                        CFG["missing_score"])

    def test_the_weak_middle_band_keeps_its_discount(self):
        self.assertLess(funding_score(None, None, 40.0, CFG),
                        CFG["missing_score"])

    def test_evidence_lifts_a_company_above_the_cap(self):
        """The cap applies only when there is nothing to go on."""
        self.assertGreater(
            funding_score(50e6, None, 450.0, CFG, years=[REC["fresh_year"]]),
            CFG["missing_score"])


class TestRecency(unittest.TestCase):

    def test_this_year_is_full_weight(self):
        self.assertEqual(recency_factor([REC["fresh_year"]], REC), REC["fresh"])
        self.assertEqual(recency_factor([REC["fresh_year"] + 1], REC),
                         REC["fresh"])

    def test_last_year_is_discounted(self):
        self.assertEqual(recency_factor([REC["fresh_year"] - 1], REC),
                         REC["recent"])

    def test_old_money_is_heavily_discounted(self):
        """Money awarded in 2011 was spent in 2011."""
        self.assertEqual(recency_factor([2011], REC), REC["stale"])

    def test_no_date_is_treated_as_old_not_fresh(self):
        self.assertEqual(recency_factor([], REC), REC["stale"])
        self.assertEqual(recency_factor([None, float("nan"), "-"], REC),
                         REC["stale"])

    def test_the_most_recent_source_wins(self):
        """A company still filing Form D is current whether or not its SBIR
        awards stopped fifteen years ago."""
        self.assertEqual(recency_factor([2011, 2003, REC["fresh_year"]], REC),
                         REC["fresh"])

    def test_same_amount_ranks_by_freshness(self):
        fresh = funding_score(200e6, None, 2.0, CFG, years=[REC["fresh_year"]])
        old = funding_score(200e6, None, 2.0, CFG, years=[2021])
        self.assertGreater(fresh, old)

    def test_an_unknown_company_is_not_pushed_below_neutral(self):
        """Recency ages real evidence. A company we know nothing about is
        already at the neutral score and must not be penalised AGAIN for
        having no date to go with the data it does not have."""
        self.assertEqual(funding_score(None, None, None, CFG, years=[]),
                         CFG["missing_score"])


class TestUniversityTies(unittest.TestCase):

    def test_a_current_partner_counts_against(self):
        """The budget and the appetite are already spoken for."""
        self.assertEqual(university_adjustment("Dartmouth College", None, UNI),
                         UNI["current_penalty"])

    def test_a_past_tie_counts_slightly_for(self):
        self.assertEqual(university_adjustment(None, "Boston University", UNI),
                         UNI["past_bonus"])

    def test_the_two_directions_are_opposite(self):
        """The asymmetry IS the rule: a live relationship is an obstacle,
        a finished one is a credential."""
        self.assertLess(UNI["current_penalty"], 0)
        self.assertGreater(UNI["past_bonus"], 0)
        self.assertGreater(abs(UNI["current_penalty"]), UNI["past_bonus"])

    def test_a_past_tie_does_not_cancel_a_current_one(self):
        both = university_adjustment("Dartmouth", "BU", UNI)
        self.assertLess(both, 0)

    def test_blank_and_nan_are_not_ties(self):
        """NaN is truthy in Python - a plain `if value` reports every blank
        cell as a university partnership."""
        for blank in (None, float("nan"), "", "   ", "-", "nan"):
            self.assertEqual(university_adjustment(blank, blank, UNI), 0.0)

    def test_a_current_tie_lowers_the_score(self):
        base = funding_score(20e6, None, 2.0, CFG, years=[REC["fresh_year"]])
        tied = funding_score(20e6, None, 2.0, CFG, years=[REC["fresh_year"]],
                             university_current="Dartmouth College")
        self.assertLess(tied, base)

    def test_a_past_tie_raises_it_a_little(self):
        base = funding_score(20e6, None, 2.0, CFG, years=[REC["fresh_year"]])
        tied = funding_score(20e6, None, 2.0, CFG, years=[REC["fresh_year"]],
                             university_past="Boston University")
        self.assertGreater(tied, base)
        self.assertLess(tied - base, 0.10)

    def test_the_adjustment_cannot_push_out_of_range(self):
        top = funding_score(1e9, 1e10, 150.0, CFG, years=[REC["fresh_year"]],
                            university_past="BU")
        self.assertLessEqual(top, 1.0)
        bottom = funding_score(None, None, 999_999.0, CFG,
                               university_current="Dartmouth")
        self.assertGreaterEqual(bottom, 0.0)


if __name__ == "__main__":
    unittest.main()
