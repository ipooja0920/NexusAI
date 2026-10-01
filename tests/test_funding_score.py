"""Tests for the data-driven funding score ("money available now") and the
academic track-record adjustment.

Both replaced judgement with arithmetic, so the thing worth pinning down is
that they stay arithmetic: same inputs, same answer, shaped by config.
The cases are the ones argued over while designing them - absence vs zero,
max vs sum, ageing towards neutral, and the caps.
"""
from __future__ import annotations

import unittest

from nexus.funding import company_academic_adjustment, company_funding_score
from nexus.scoring import academic_adjustment, log_score, money_score, recency_factor
from nexus.settings import Settings

CFG = Settings().funding
REC = CFG["recency"]
ACA = Settings().academic
FRESH = REC["fresh_year"]


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



class TestMoneyScore(unittest.TestCase):

    def sig(self, **kw):
        base = {k: (None, None) for k in CFG["signals"]}
        base.update(kw)
        return base

    def test_no_signal_is_neutral(self):
        self.assertEqual(money_score(self.sig(), CFG), (CFG["missing_score"], None))

    def test_formd_raised_bonus_only_without_signals(self):
        self.assertEqual(money_score(self.sig(), CFG, formd_raised_small=True)[0],
                         CFG["missing_score"] + CFG["formd_raised_bonus"])
        with_sig = money_score(self.sig(formd_recent=(20, FRESH)), CFG, formd_raised_small=True)
        self.assertEqual(with_sig[0], 1.0)

    def test_each_signal_full_marks_at_its_high_end(self):
        for name, lim in CFG["signals"].items():
            self.assertEqual(money_score(self.sig(**{name: (lim["hi"], FRESH)}), CFG),
                             (1.0, name))

    def test_best_signal_counts_not_the_sum(self):
        one = money_score(self.sig(cash_total=(50, FRESH)), CFG)[0]
        two = money_score(self.sig(cash_total=(50, FRESH), formd_recent=(1, FRESH)), CFG)[0]
        self.assertEqual(one, two)

    def test_old_money_shrinks_towards_neutral_not_below(self):
        fresh = money_score(self.sig(rd_expense=(50, FRESH)), CFG)[0]
        old = money_score(self.sig(rd_expense=(50, 2019)), CFG)[0]
        self.assertGreater(fresh, old)
        self.assertGreaterEqual(old, CFG["missing_score"])

    def test_tiny_money_reads_tiny(self):
        """A $0.5m raise is evidence of little money - below an unknown."""
        self.assertLess(money_score(self.sig(formd_recent=(0.5, FRESH)), CFG)[0],
                        CFG["missing_score"])

    def test_oa_funded_recently_is_full_marks(self):
        self.assertEqual(money_score(self.sig(), CFG, oa_funded_recent=True), (1.0, "oa_funded"))


class TestCompanyFundingScore(unittest.TestCase):

    def test_reads_the_combined_columns(self):
        row = {"formd_recent_usdmm": 5.0, "xbrl_cash_total_usdmm": None,
               "sbir_active_usdmm": 1.2, "revenue_usdmm": None}
        score, reason = company_funding_score(row, CFG)
        self.assertGreater(score, CFG["missing_score"])
        self.assertIn("raised $5.0m since 2025", reason)
        self.assertIn("SBIR", reason)

    def test_cash_aged_by_its_date(self):
        new = company_funding_score({"xbrl_cash_total_usdmm": 80, "xbrl_cash_as_of": "2026-06-30"}, CFG)[0]
        old = company_funding_score({"xbrl_cash_total_usdmm": 80, "xbrl_cash_as_of": "2023-12-31"}, CFG)[0]
        self.assertGreater(new, old)

    def test_oa_funded_needs_a_recent_year(self):
        recent = {"oa_funded_university": True, "oa_last_funded_year": 2025}
        old = {"oa_funded_university": True, "oa_last_funded_year": 2019}
        self.assertEqual(company_funding_score(recent, CFG)[0], 1.0)
        self.assertEqual(company_funding_score(old, CFG)[0], CFG["missing_score"])

    def test_formd_raised_bonus_only_for_small_or_unknown(self):
        small = company_funding_score({"formd_raised": "Yes", "revenue_usdmm": 12}, CFG)[0]
        big = company_funding_score({"formd_raised": "Yes", "revenue_usdmm": 400}, CFG)[0]
        self.assertGreater(small, big)
        self.assertEqual(big, CFG["missing_score"])

    def test_blank_and_nan_are_no_record(self):
        row = {k: float("nan") for k in ("formd_recent_usdmm", "xbrl_cash_total_usdmm",
                                         "xbrl_rd_expense_usdmm", "sbir_active_usdmm")}
        self.assertEqual(company_funding_score(row, CFG),
                         (CFG["missing_score"], "No funding record found - neutral."))


class TestAcademicAdjustment(unittest.TestCase):

    def test_bonuses_add_up_to_the_cap(self):
        all_on = {k: True for k in ACA["bonus"]}
        self.assertEqual(academic_adjustment(all_on, 0, ACA)[0], ACA["bonus_cap"])

    def test_penalties_capped(self):
        all_on = {k: True for k in ACA["penalty"]}
        self.assertEqual(academic_adjustment(all_on, 0, ACA)[0],
                         max(sum(ACA["penalty"].values()), ACA["penalty_cap"]))

    def test_current_partner_counts_against(self):
        self.assertLess(academic_adjustment({"ct_university_current": True}, 0, ACA)[0], 0)

    def test_trials_not_penalised_by_default(self):
        self.assertEqual(academic_adjustment({}, 50, ACA), (0.0, []))

    def test_trials_penalty_when_switched_on(self):
        cfg = dict(ACA, ct_active_penalty=-0.05, ct_active_above=10)
        self.assertEqual(academic_adjustment({}, 11, cfg)[0], -0.05)
        self.assertEqual(academic_adjustment({}, 10, cfg)[0], 0.0)

    def test_row_level_reads_the_combined_columns(self):
        row = {"oa_funded_university": True, "university_past": "Yale University",
               "ct_university_current": "UConn Health", "sbir_awarded": "Yes"}
        adj, reason = company_academic_adjustment(row, ACA)
        self.assertAlmostEqual(adj, 0.15 - 0.05)
        self.assertIn("current clinical-trial university partner", reason)

    def test_nothing_known(self):
        self.assertEqual(company_academic_adjustment({"university_past": float("nan")}, ACA),
                         (0.0, "No academic track record found."))


if __name__ == "__main__":
    unittest.main()
