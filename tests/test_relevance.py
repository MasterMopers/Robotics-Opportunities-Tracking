import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from lib.enrich import extract_signals
from lib.relevance import score_relevance

RULES = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules.yaml")))


class TestSubstringRegressionFix(unittest.TestCase):
    """Named regression: naive substring matching on "stem" produced three
    false positives from the words "system" and "ecosystem" in a real
    corpus. Word-boundary matching (\\b...\\b) closes that off. Verified
    here against the shared extract_signals() phrase matcher, which
    lib/relevance.py's score_relevance() uses the identical technique for."""

    def setUp(self):
        self.stem_rules = {
            "signals": {
                "contest": [{"phrase": "stem", "weight": 1}],
                "grant": [],
            },
            "reject_rules": {"contest": [], "grant": []},
        }

    def test_stem_does_not_match_inside_system(self):
        matched, scores, reject = extract_signals("This is an operating system.", self.stem_rules)
        self.assertNotIn("stem", matched["contest"])
        self.assertEqual(scores["contest"], 0.0)

    def test_stem_does_not_match_inside_ecosystem(self):
        matched, scores, reject = extract_signals("Grow the robotics ecosystem.", self.stem_rules)
        self.assertNotIn("stem", matched["contest"])
        self.assertEqual(scores["contest"], 0.0)

    def test_stem_matches_as_whole_word(self):
        matched, scores, reject = extract_signals("A STEM outreach program for kids.", self.stem_rules)
        self.assertIn("stem", matched["contest"])
        self.assertEqual(scores["contest"], 1.0)


class TestRelevanceBucketModel(unittest.TestCase):
    def test_core_alone_qualifies(self):
        result = score_relevance("Build an autonomous robot arm for this challenge.", RULES)
        self.assertGreaterEqual(result["relevance_core_hits"], 1)
        self.assertTrue(result["relevance_eligible"])

    def test_two_non_core_buckets_qualify(self):
        # mechanical (CAD) + electrical (Arduino), zero core terms.
        text = "Design your project in CAD and bring it to life with an Arduino."
        result = score_relevance(text, RULES)
        self.assertEqual(result["relevance_core_hits"], 0)
        self.assertGreaterEqual(len(result["relevance_buckets"]), 2)
        self.assertTrue(result["relevance_eligible"])

    def test_one_non_core_bucket_alone_does_not_qualify(self):
        # software bucket only (reinforcement learning), no core, no second
        # discipline -- this is exactly the "generic AI hackathon" case the
        # bucket model exists to keep out.
        text = "A hackathon about reinforcement learning and nothing else."
        result = score_relevance(text, RULES)
        self.assertEqual(result["relevance_core_hits"], 0)
        self.assertEqual(len(result["relevance_buckets"]), 1)
        self.assertFalse(result["relevance_eligible"])

    def test_anti_terms_subtract(self):
        with_anti = score_relevance("A robot hackathon, but really it's a web3 blockchain NFT project.", RULES)
        without_anti = score_relevance("A robot hackathon.", RULES)
        self.assertLess(with_anti["relevance_score"], without_anti["relevance_score"])

    def test_real_generic_ai_hackathon_scores_below_floor(self):
        # Real title+snippet from the current DB (LA Hacks AI Hackathon
        # 2026 / "OCT 17 - 18 Los Angeles, CA") -- no core robotics term, no
        # hardware discipline signal at all, purely a generic AI hackathon.
        text = "LA Hacks AI Hackathon 2026 OCT 17 - 18 Los Angeles, CA"
        result = score_relevance(text, RULES)
        self.assertEqual(result["relevance_score"], 0.0)
        # relevance_floor is derived by scripts/calibrate_floor.py from the
        # calibration labels and is always >= 0 by construction (the sweep
        # only considers non-negative integer floors), so a score of 0 with
        # zero core hits and zero buckets is below floor regardless of the
        # specific derived value.
        self.assertFalse(result["relevance_eligible"])


if __name__ == "__main__":
    unittest.main()
