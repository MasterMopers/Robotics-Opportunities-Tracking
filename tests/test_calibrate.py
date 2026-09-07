import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import calibrate_floor  # noqa: E402


def _row(floor, tp, fp, tn, fn):
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"floor": floor, "tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


class TestSelectFloor(unittest.TestCase):
    def test_picks_precision_maximizing_floor_subject_to_recall_constraint(self):
        # floor=0: perfect recall but so-so precision. floor=1: recall dips
        # just below the 0.9 constraint but precision would be higher --
        # must NOT be picked because it violates the constraint. floor=-1:
        # meets the constraint with lower precision than floor=0.
        table = [
            _row(-1, tp=9, fp=3, tn=7, fn=1),   # recall=0.9, precision=0.75
            _row(0, tp=9, fp=1, tn=9, fn=1),    # recall=0.9, precision=0.90 <- best feasible
            _row(1, tp=7, fp=0, tn=10, fn=3),   # recall=0.7 -- infeasible
        ]
        selected, feasible = calibrate_floor.select_floor(table)
        self.assertTrue(feasible)
        self.assertEqual(selected["floor"], 0)
        self.assertAlmostEqual(selected["precision"], 0.9)

    def test_falls_back_to_f1_when_recall_constraint_infeasible(self):
        # No floor reaches recall >= 0.9 -- must fall back to max F1.
        table = [
            _row(0, tp=5, fp=5, tn=5, fn=5),   # precision=0.5 recall=0.5 f1=0.5
            _row(1, tp=8, fp=1, tn=9, fn=2),   # precision=0.89 recall=0.8 f1=0.84 <- best f1
            _row(2, tp=3, fp=0, tn=10, fn=7),  # precision=1.0 recall=0.3 f1=0.46
        ]
        selected, feasible = calibrate_floor.select_floor(table)
        self.assertFalse(feasible)
        self.assertEqual(selected["floor"], 1)

    def test_ties_prefer_higher_floor(self):
        table = [
            _row(0, tp=10, fp=1, tn=9, fn=0),
            _row(1, tp=10, fp=1, tn=9, fn=0),  # identical precision/recall, higher floor
        ]
        selected, feasible = calibrate_floor.select_floor(table)
        self.assertTrue(feasible)
        self.assertEqual(selected["floor"], 1)


class TestSweepEndToEnd(unittest.TestCase):
    def setUp(self):
        self.rules = {
            "signals": {
                "relevance": {
                    "core": ["robot"],
                    "mechanical": ["cad"],
                    "electrical": ["arduino"],
                    "software": ["computer vision"],
                    "anti": ["blockchain"],
                }
            },
            "thresholds": {"relevance_floor": 0},
        }
        self.labels = [
            {"relevant": True, "text": "Build a robot for this challenge."},   # core=1 -> r=3
            {"relevant": True, "text": "Use CAD and Arduino to build a device."},  # 2 buckets -> r=4
            {"relevant": False, "text": "A generic computer vision hackathon."},  # 1 bucket -> r=2, ineligible
            {"relevant": False, "text": "A blockchain and web3 pitch competition."},  # anti only -> r=-2
        ]

    def test_sweep_produces_monotonic_floor_range_and_a_perfect_floor_exists(self):
        table = calibrate_floor.sweep(self.labels, self.rules)
        floors = [row["floor"] for row in table]
        self.assertEqual(floors, list(range(min(floors), max(floors) + 1)))
        # At floor=3, both true positives (r=3, r=4) are captured and both
        # true negatives correctly excluded (r=2 fails the bucket-count
        # disjunction, r=-2 fails both the floor and the disjunction) --
        # this should be a perfect-precision, perfect-recall floor.
        row3 = next(r for r in table if r["floor"] == 3)
        self.assertEqual(row3["precision"], 1.0)
        self.assertEqual(row3["recall"], 1.0)

    def test_select_floor_on_real_sweep_hits_recall_constraint(self):
        table = calibrate_floor.sweep(self.labels, self.rules)
        selected, feasible = calibrate_floor.select_floor(table)
        self.assertTrue(feasible)
        self.assertEqual(selected["precision"], 1.0)
        self.assertEqual(selected["recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
