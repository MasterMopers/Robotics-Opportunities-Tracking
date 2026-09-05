import math
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.rank import NO_DEADLINE_URGENCY, compute_fit_scores, parse_money_value

WEIGHTS = {
    "relevance_weight": 0.40,
    "eligibility_weight": 0.30,
    "money_weight": 0.15,
    "deadline_weight": 0.15,
}

TODAY = date(2026, 1, 1)


def _row(title, relevance_score=5.0, eligibility="eligible", money_raw=None, deadline_date=None):
    return {
        "title": title,
        "relevance_score": relevance_score,
        "eligibility": eligibility,
        "money_raw": money_raw,
        "deadline_date": deadline_date,
    }


class TestParseMoneyValue(unittest.TestCase):
    def test_simple_amount(self):
        self.assertEqual(parse_money_value("$5,000"), 5000.0)

    def test_k_suffix_multiplies(self):
        self.assertEqual(parse_money_value("$5k"), 5000.0)

    def test_range_takes_max(self):
        self.assertEqual(parse_money_value("$1,000-100,000"), 100000.0)

    def test_none_when_missing(self):
        self.assertIsNone(parse_money_value(None))
        self.assertIsNone(parse_money_value(""))
        self.assertIsNone(parse_money_value("amount not extracted"))


class TestComputeFitScores(unittest.TestCase):
    def test_higher_relevance_ranks_first_all_else_equal(self):
        low = _row("Low relevance", relevance_score=1.0)
        high = _row("High relevance", relevance_score=10.0)
        scored = compute_fit_scores([low, high], WEIGHTS, today=TODAY)
        self.assertEqual([r["title"] for r, _fit in scored], ["High relevance", "Low relevance"])

    def test_eligible_outranks_unknown_all_else_equal(self):
        unknown = _row("Unknown elig", eligibility="unknown")
        eligible = _row("Eligible", eligibility="eligible")
        scored = compute_fit_scores([unknown, eligible], WEIGHTS, today=TODAY)
        self.assertEqual([r["title"] for r, _fit in scored], ["Eligible", "Unknown elig"])

    def test_missing_deadline_takes_u_half(self):
        rolling = _row("Rolling grant", deadline_date=None)
        far_off = _row("Far deadline", deadline_date="2027-01-01")  # ~365 days out, u ~ 0
        near = _row("Near deadline", deadline_date="2026-01-08")  # 7 days out, u close to 1

        scored = {r["title"]: fit for r, fit in compute_fit_scores([rolling, far_off, near], WEIGHTS, today=TODAY)}

        # u=0.5 for rolling should land it between the far-off (low u) and
        # near (high u) deadline rows, since relevance/eligibility/money
        # are identical across all three.
        self.assertLess(scored["Far deadline"], scored["Rolling grant"])
        self.assertLess(scored["Rolling grant"], scored["Near deadline"])

    def test_missing_money_scores_zero_not_average(self):
        no_money = _row("No money stated", money_raw=None)
        some_money = _row("Some money", money_raw="$100")
        big_money = _row("Big money", money_raw="$100,000")

        scored = {r["title"]: fit for r, fit in compute_fit_scores([no_money, some_money, big_money], WEIGHTS, today=TODAY)}

        # no_money's fit must equal what big_money/some_money would get
        # with money_weight contribution forced to exactly 0 -- not
        # something in between (which an "average" fallback would produce).
        expected_no_money_fit = (
            WEIGHTS["relevance_weight"] * 1.0
            + WEIGHTS["eligibility_weight"] * 1.0
            + WEIGHTS["money_weight"] * 0.0
            + WEIGHTS["deadline_weight"] * NO_DEADLINE_URGENCY
        )
        self.assertAlmostEqual(scored["No money stated"], expected_no_money_fit, places=9)
        self.assertLess(scored["No money stated"], scored["Some money"])
        self.assertLess(scored["Some money"], scored["Big money"])

    def test_relevance_normalized_over_the_given_set(self):
        # r_hat is min-max normalized over the SET passed in, not a fixed
        # global scale -- the lowest-scoring item in a set should always
        # get r_hat=0 and the highest r_hat=1, regardless of the raw values.
        a = _row("A", relevance_score=2.0)
        b = _row("B", relevance_score=8.0)
        scored = compute_fit_scores([a, b], WEIGHTS, today=TODAY)
        fits = {r["title"]: fit for r, fit in scored}
        expected_a = WEIGHTS["eligibility_weight"] * 1.0 + WEIGHTS["deadline_weight"] * NO_DEADLINE_URGENCY
        expected_b = (
            WEIGHTS["relevance_weight"] * 1.0
            + WEIGHTS["eligibility_weight"] * 1.0
            + WEIGHTS["deadline_weight"] * NO_DEADLINE_URGENCY
        )
        self.assertAlmostEqual(fits["A"], expected_a, places=9)
        self.assertAlmostEqual(fits["B"], expected_b, places=9)

    def test_tied_relevance_scores_all_get_full_r_hat(self):
        a = _row("A", relevance_score=5.0)
        b = _row("B", relevance_score=5.0)
        scored = compute_fit_scores([a, b], WEIGHTS, today=TODAY)
        fits = [fit for _r, fit in scored]
        self.assertAlmostEqual(fits[0], fits[1], places=9)


if __name__ == "__main__":
    unittest.main()
