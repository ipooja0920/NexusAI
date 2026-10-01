"""Unit tests for the scoring math — the deterministic, no-AI parts.

These are the formulas carried over from ProgramTesting9. They decide 45%
of every company's final score, so they're worth pinning down exactly.
"""
import math
import unittest

from nexus.scoring import (STD_NA_REASON, _parse_score_reason, distance_score,
                           employee_score, normalize, parse_employees,
                           parse_revenue, revenue_score)

REV_CFG = {"small_max": 5, "small_score": 0.2, "mid_score": 0.4, "sweet_lo": 100,
           "sweet_hi": 1000, "big_hi": 5000, "big_floor": 0.2, "missing_score": 0.5}


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

    def test_zero_distance_is_on_campus_not_no_data(self):
        """Reversed from v1, where 0.0 was the sentinel for "no distance".

        It is not any more: distance_miles() returns None when it cannot
        locate a company, and enrich_companies initialises dist to None. So
        0.0 can only mean a genuine zero-mile distance - and treating the
        best possible answer as the worst inverted the 25% distance weight
        for anything sitting in Storrs."""
        self.assertEqual(distance_score(0.0, 400.0), 1.0)

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
    """Size fit: full marks $100m - $1bn ("at least $100m"), giants marked
    down but not zeroed, small companies left to the funding score."""

    def test_missing_is_neutral(self):
        self.assertEqual(revenue_score(None, REV_CFG), 0.5)
        self.assertEqual(revenue_score(float("nan"), REV_CFG), 0.5)
        self.assertEqual(revenue_score("-", REV_CFG), 0.5)

    def test_small_and_mid(self):
        self.assertEqual(revenue_score(0.0, REV_CFG), 0.2)
        self.assertEqual(revenue_score(4.9, REV_CFG), 0.2)
        self.assertEqual(revenue_score(5.0, REV_CFG), 0.4)
        self.assertEqual(revenue_score(99.0, REV_CFG), 0.4)

    def test_sweet_spot_is_full_marks(self):
        for v in (100, 450, 1000):
            self.assertEqual(revenue_score(v, REV_CFG), 1.0)

    def test_500m_now_beats_40m(self):
        """The old curve peaked at $30-60m and hit zero at $1bn."""
        self.assertGreater(revenue_score(500, REV_CFG), revenue_score(40, REV_CFG))

    def test_taper_then_floor_for_giants(self):
        self.assertAlmostEqual(revenue_score(3000, REV_CFG), 0.6)
        self.assertEqual(revenue_score(5000, REV_CFG), 0.2)
        self.assertEqual(revenue_score(60_000, REV_CFG), 0.2)
        falling = [revenue_score(v, REV_CFG) for v in (1000, 2000, 3500, 4999)]
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


class TestDistanceAtTheReferencePoint(unittest.TestCase):
    """Zero miles is the BEST answer, not a missing one."""

    def test_zero_miles_is_the_top_score(self):
        """A company in Storrs used to score 0.0 for proximity - the same
        as one we could not locate at all - because `miles == 0.0` was
        lumped in with `miles is None`. That inverted the whole point of a
        25% distance weight."""
        self.assertEqual(distance_score(0.0, 400.0), 1.0)

    def test_unknown_distance_still_scores_zero(self):
        self.assertEqual(distance_score(None, 400.0), 0.0)

    def test_zero_beats_any_real_distance(self):
        self.assertGreater(distance_score(0.0, 400.0),
                           distance_score(1.0, 400.0))

    def test_nan_and_junk_are_unknown(self):
        self.assertEqual(distance_score(float("nan"), 400.0), 0.0)
        self.assertEqual(distance_score("-", 400.0), 0.0)
        self.assertEqual(distance_score(-5.0, 400.0), 0.0)


if __name__ == "__main__":
    unittest.main()


class TestCacheOnlyWritesWhenChanged(unittest.TestCase):
    """embeddings.json reaches 724MB on a full datasource, and enrichment
    saves every 25 rows so an interrupt loses nothing. On a re-run where
    everything is already cached, that was 872 rewrites of an unchanged file
    - the entire cost of a 2.5 hour run."""

    def _cache(self):
        import tempfile
        from pathlib import Path
        from nexus.cache import JsonCache
        d = Path(tempfile.mkdtemp(prefix="nexusai_cache_"))
        return JsonCache(d / "c.json")

    def test_a_clean_cache_does_not_rewrite_the_file(self):
        c = self._cache()
        c.set("k", [1, 2, 3])
        c.save()
        first = c.path.stat().st_mtime_ns
        c.save()
        self.assertEqual(c.path.stat().st_mtime_ns, first)

    def test_setting_the_same_value_is_not_a_change(self):
        c = self._cache()
        c.set("k", [1, 2, 3])
        c.save()
        first = c.path.stat().st_mtime_ns
        c.set("k", [1, 2, 3])
        c.save()
        self.assertEqual(c.path.stat().st_mtime_ns, first)

    def test_a_real_change_is_written(self):
        from nexus.cache import JsonCache
        c = self._cache()
        c.set("k", [1, 2, 3])
        c.save()
        c.set("k2", [9])
        c.save()
        self.assertEqual(JsonCache(c.path).get("k2"), [9])

    def test_a_changed_value_is_written(self):
        from nexus.cache import JsonCache
        c = self._cache()
        c.set("k", [1])
        c.save()
        c.set("k", [2])
        c.save()
        self.assertEqual(JsonCache(c.path).get("k"), [2])

    def test_a_never_saved_cache_still_writes_the_first_time(self):
        """Skipping on 'not dirty' must not skip creating the file at all."""
        c = self._cache()
        c.save()
        self.assertTrue(c.path.exists())
