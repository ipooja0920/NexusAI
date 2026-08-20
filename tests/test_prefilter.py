"""Tests for the embedding pre-filter and the geocoding/address helpers."""
import unittest

from nexus.geo import clean_address_block, distance_miles, select_address_block
from nexus.prefilter import (company_embedding_text, rank_by_similarity,
                             select_candidates)


class TestCompanyEmbeddingText(unittest.TestCase):
    def test_name_and_description_joined(self):
        t = company_embedding_text("Acme Corp", "Makes catalysts.")
        self.assertIn("Acme Corp", t)
        self.assertIn("Makes catalysts.", t)

    def test_dash_description_dropped(self):
        t = company_embedding_text("Acme Corp", "-")
        self.assertEqual(t, "Acme Corp.")

    def test_missing_description_variants(self):
        for desc in [None, "", "   ", "nan"]:
            self.assertEqual(company_embedding_text("Acme Corp", desc), "Acme Corp.")

    def test_never_returns_empty_when_name_present(self):
        """Important: a company with no description still gets embeddable
        text (its name), so it receives a real vector rather than None."""
        self.assertTrue(company_embedding_text("Acme Corp", None).strip())


class TestRankBySimilarity(unittest.TestCase):
    def test_identical_vectors_score_one(self):
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(rank_by_similarity(v, [v])[0], 1.0, places=5)

    def test_orthogonal_vectors_score_zero(self):
        self.assertAlmostEqual(rank_by_similarity([1.0, 0.0], [[0.0, 1.0]])[0], 0.0, places=5)

    def test_magnitude_does_not_matter_only_direction(self):
        """Cosine measures angle: a doubled vector is equally similar."""
        a = rank_by_similarity([1.0, 1.0], [[1.0, 1.0]])[0]
        b = rank_by_similarity([1.0, 1.0], [[5.0, 5.0]])[0]
        self.assertAlmostEqual(a, b, places=5)

    def test_missing_vector_ranks_last(self):
        sims = rank_by_similarity([1.0, 0.0], [[1.0, 0.0], None, [0.0, 1.0]])
        self.assertEqual(sims[1], -1.0)
        self.assertEqual(min(sims), -1.0)

    def test_zero_vector_does_not_divide_by_zero(self):
        sims = rank_by_similarity([1.0, 0.0], [[0.0, 0.0]])
        self.assertEqual(sims[0], -1.0)

    def test_more_similar_text_scores_higher(self):
        from tests.harness import fake_embedding
        profile = fake_embedding("heterogeneous catalysis zeolite pyrolysis fuel")
        relevant = fake_embedding("Catalyst Works. zeolite catalysts for fuel upgrading")
        irrelevant = fake_embedding("Dental Supplies Co. dental equipment distributor")
        sims = rank_by_similarity(profile, [relevant, irrelevant])
        self.assertGreater(sims[0], sims[1])


class TestSelectCandidates(unittest.TestCase):
    def test_takes_top_n_in_descending_order(self):
        idx = select_candidates([0.1, 0.9, 0.5, 0.7], 2)
        self.assertEqual(idx, [1, 3])

    def test_zero_means_take_everything(self):
        idx = select_candidates([0.1, 0.9, 0.5], 0)
        self.assertEqual(sorted(idx), [0, 1, 2])

    def test_cutoff_larger_than_list_returns_all(self):
        idx = select_candidates([0.1, 0.9], 500)
        self.assertEqual(len(idx), 2)

    def test_ties_do_not_lose_companies(self):
        idx = select_candidates([0.5, 0.5, 0.5, 0.5], 2)
        self.assertEqual(len(idx), 2)
        self.assertEqual(len(set(idx)), 2)


class TestAddressCleaning(unittest.TestCase):
    def test_strips_headquarters_country_and_phone(self):
        raw = ("Headquarters\n82 Crenshaw Dr.\nFlanders, New Jersey 07836\n"
               "United States\nMain Phone: 555-0100")
        cleaned = clean_address_block(raw)
        self.assertNotIn("Headquarters", cleaned)
        self.assertNotIn("555-0100", cleaned)
        self.assertIn("Crenshaw", cleaned)
        self.assertIn("Flanders", cleaned)
        self.assertTrue(cleaned.endswith("USA"))

    def test_none_and_empty(self):
        self.assertIsNone(clean_address_block(None))
        self.assertIsNone(clean_address_block(""))
        self.assertIsNone(clean_address_block("Headquarters\nUnited States"))

    def test_prefers_headquarters_block_when_several(self):
        raw = ("Branch Office\n5 Side St\nBoston, Massachusetts\n\n"
               "Headquarters\n1 Main St\nHartford, Connecticut")
        selected = select_address_block(raw)
        self.assertIn("Hartford", selected)
        self.assertNotIn("Boston", selected)

    def test_single_block_passes_through(self):
        raw = "1 Main St\nHartford, Connecticut"
        self.assertIn("Hartford", select_address_block(raw))


class TestDistanceMiles(unittest.TestCase):
    STORRS = (41.8073, -72.2536)

    def test_same_point_is_zero(self):
        self.assertAlmostEqual(distance_miles(41.8073, -72.2536, self.STORRS), 0.0, places=2)

    def test_none_coordinates(self):
        self.assertIsNone(distance_miles(None, -72.2, self.STORRS))
        self.assertIsNone(distance_miles(41.8, None, self.STORRS))

    def test_known_distance_hartford(self):
        # Hartford CT is roughly 25 miles from Storrs
        d = distance_miles(41.7637, -72.6851, self.STORRS)
        self.assertGreater(d, 15)
        self.assertLess(d, 35)


if __name__ == "__main__":
    unittest.main()
