"""Unit tests for the scoring math — the deterministic, no-AI parts.

These are the formulas carried over from ProgramTesting9. They decide 45%
of every company's final score, so they're worth pinning down exactly.
"""
import math
import unittest

from nexus.scoring import (STD_NA_REASON, _parse_score_reason, distance_score,
                           employee_score, normalize, parse_employees,
                           parse_revenue, revenue_score)

REV_CFG = {"rev_low_zero": 0.0, "rev_low_full": 30.0, "rev_high_full": 60.0,
           "rev_high_zero": 1000.0, "rev_missing_score": 0.5}


class TestParseRevenue(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(parse_revenue("38.5"), 38.5)

    def test_thousands_separator(self):
        self.assertEqual(parse_revenue("66,442.9"), 66442.9)

    def test_dash_is_missing(self):
        self.assertIsNone(parse_revenue("-"))

    def test_blank_and_nan_are_missing(self):
        self.assertIsNone(parse_revenue(""))
        self.assertIsNone(parse_revenue("   "))
        self.assertIsNone(parse_revenue("nan"))
        self.assertIsNone(parse_revenue("NaN"))
        self.assertIsNone(parse_revenue(None))

    def test_junk_is_missing_not_a_crash(self):
        self.assertIsNone(parse_revenue("about $5 million"))

    def test_float_input(self):
        self.assertEqual(parse_revenue(38.5), 38.5)


class TestParseEmployees(unittest.TestCase):
    def test_plain_and_comma(self):
        self.assertEqual(parse_employees("25"), 25.0)
        self.assertEqual(parse_employees("5,200"), 5200.0)

    def test_missing_becomes_zero(self):
        # Employees differ from revenue: missing means 0, not None,
        # because the employee component has no "neutral" score.
        self.assertEqual(parse_employees("-"), 0.0)
        self.assertEqual(parse_employees(""), 0.0)
        self.assertEqual(parse_employees(None), 0.0)
        self.assertEqual(parse_employees("nan"), 0.0)

    def test_junk_becomes_zero(self):
        self.assertEqual(parse_employees("a few dozen"), 0.0)


class TestDistanceScore(unittest.TestCase):
    def test_missing_distance_scores_zero(self):
        self.assertEqual(distance_score(None, 400.0), 0.0)

    def test_zero_distance_scores_zero(self):
        # Carried over from v1: distance 0 means "no data", not "on campus".
        self.assertEqual(distance_score(0.0, 400.0), 0.0)

    def test_decay_shape(self):
        near = distance_score(10, 400.0)
        mid = distance_score(200, 400.0)
        far = distance_score(3000, 400.0)
        self.assertGreater(near, mid)
        self.assertGreater(mid, far)
        self.assertAlmostEqual(near, math.exp(-10 / 400), places=9)
        self.assertLess(far, 0.01)   # Germany is effectively zero

    def test_bounded_0_to_1(self):
        for d in [1, 50, 400, 5000]:
            s = distance_score(d, 400.0)
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)


class TestEmployeeScore(unittest.TestCase):
    def test_below_cap_is_proportional(self):
        self.assertAlmostEqual(employee_score(250, 1000), 0.25)

    def test_at_and_above_cap_is_one(self):
        self.assertEqual(employee_score(1000, 1000), 1.0)
        self.assertEqual(employee_score(100000, 1000), 1.0)

    def test_zero_is_zero(self):
        self.assertEqual(employee_score(0, 1000), 0.0)


class TestRevenueScore(unittest.TestCase):
    def test_missing_is_neutral(self):
        self.assertEqual(revenue_score(None, REV_CFG), 0.5)

    def test_zero_revenue(self):
        self.assertEqual(revenue_score(0.0, REV_CFG), 0.0)

    def test_ramp_up_below_ideal(self):
        self.assertAlmostEqual(revenue_score(15.0, REV_CFG), 0.5)

    def test_ideal_band_is_full_marks(self):
        self.assertEqual(revenue_score(30.0, REV_CFG), 1.0)
        self.assertEqual(revenue_score(45.0, REV_CFG), 1.0)
        self.assertEqual(revenue_score(60.0, REV_CFG), 1.0)

    def test_decline_above_ideal(self):
        mid = revenue_score(530.0, REV_CFG)     # halfway 60 -> 1000
        self.assertGreater(mid, 0.4)
        self.assertLess(mid, 0.6)

    def test_very_large_revenue_scores_zero(self):
        # This is the deliberate big-company penalty.
        self.assertEqual(revenue_score(1000.0, REV_CFG), 0.0)
        self.assertEqual(revenue_score(500_000.0, REV_CFG), 0.0)

    def test_monotonic_within_each_side(self):
        rising = [revenue_score(v, REV_CFG) for v in [1, 5, 10, 20, 29]]
        self.assertEqual(rising, sorted(rising))
        falling = [revenue_score(v, REV_CFG) for v in [61, 200, 600, 999]]
        self.assertEqual(falling, sorted(falling, reverse=True))


class TestNormalize(unittest.TestCase):
    def test_basic_min_max(self):
        self.assertEqual(normalize([1, 5, 9]), [0.0, 0.5, 1.0])

    def test_none_becomes_zero(self):
        # An "NA" company must not be rewarded — it lands at the bottom.
        out = normalize([None, 5, 9])
        self.assertEqual(out[0], 0.0)
        self.assertEqual(out[2], 1.0)

    def test_all_none(self):
        self.assertEqual(normalize([None, None]), [0.0, 0.0])

    def test_empty_list(self):
        self.assertEqual(normalize([]), [])

    def test_constant_series_does_not_divide_by_zero(self):
        self.assertEqual(normalize([7, 7, 7]), [0.0, 0.0, 0.0])


class TestParseScoreReason(unittest.TestCase):
    def test_valid_json(self):
        score, reason = _parse_score_reason('{"company":"X","score":7,"reason":"good fit"}')
        self.assertEqual(score, "7")
        self.assertEqual(reason, "good fit")

    def test_explicit_na(self):
        score, reason = _parse_score_reason('{"company":"X","score":"NA","reason":"whatever"}')
        self.assertEqual(score, "NA")
        self.assertEqual(reason, STD_NA_REASON)

    def test_malformed_json_becomes_na(self):
        score, reason = _parse_score_reason("not json at all")
        self.assertEqual(score, "NA")
        self.assertEqual(reason, STD_NA_REASON)

    def test_out_of_range_scores_are_clamped(self):
        self.assertEqual(_parse_score_reason('{"score":50,"reason":"r"}')[0], "9")
        self.assertEqual(_parse_score_reason('{"score":-3,"reason":"r"}')[0], "1")

    def test_missing_reason_gets_placeholder(self):
        score, reason = _parse_score_reason('{"score":5}')
        self.assertEqual(score, "5")
        self.assertEqual(reason, "No reason provided")

    def test_unexpected_score_type_becomes_na(self):
        self.assertEqual(_parse_score_reason('{"score":{"a":1},"reason":"r"}')[0], "NA")


if __name__ == "__main__":
    unittest.main()
