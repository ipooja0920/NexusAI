"""Pre-filter places: companies and hospitals/clinics are shortlisted
SEPARATELY, so PT clinics (which only compete for the Hospitals & Clinics
sheet) never take a company's place. Cristina Colón-Semenza: 243 of 510
places had gone to clinics, and Whoop, ReWalk and Sword Health were never
scored."""
import unittest

from match_faculty import combine_searches, shortlist

PROV = {1, 2, 3, 4, 5}                      # indexes that are clinics


def is_prov(gi):
    return gi in PROV


class TestShortlist(unittest.TestCase):

    def test_clinics_do_not_take_company_places(self):
        # by the full profile the clinics rank first
        profile = [1, 2, 3, 4, 5, 10, 11, 12, 13, 14]
        old, _ = combine_searches([("profile", profile)], top_n=4, extra=0)
        self.assertEqual(old, [1, 2, 3, 4])                     # before: all clinics
        chosen, by, n_c, n_p = shortlist([("profile", profile)], is_prov, 4, 0, 2, 0)
        self.assertEqual([g for g in chosen if not is_prov(g)], [10, 11, 12, 13])
        self.assertEqual([g for g in chosen if is_prov(g)], [1, 2])
        self.assertEqual((n_c, n_p), (4, 2))

    def test_keyword_places_split_too(self):
        profile = [10, 1]
        kw = [2, 3, 20, 21, 22]
        chosen, by, n_c, n_p = shortlist([("profile", profile), ("keyword: x", kw)],
                                          is_prov, top_n=1, extra=2, prov_n=1, prov_extra=1)
        self.assertEqual(sorted(g for g in chosen if not is_prov(g)), [10, 20, 21])
        self.assertEqual(sorted(g for g in chosen if is_prov(g)), [1, 2])
        self.assertEqual(by[20], "keyword: x")
        self.assertEqual(by[1], "profile")

    def test_fewer_than_places(self):
        chosen, _, n_c, n_p = shortlist([("profile", [10, 1])], is_prov, 300, 30, 100, 10)
        self.assertEqual((sorted(chosen), n_c, n_p), ([1, 10], 1, 1))


if __name__ == "__main__":
    unittest.main()
