import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor import needs_reenrichment


def _row(**overrides):
    base = {
        "deadline_confidence": "explicit",
        "relevance_score": 5.0,
        "enriched": 1,
        "last_enriched": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


class TestNeedsReenrichment(unittest.TestCase):
    def test_fully_resolved_recent_row_does_not_need_reenrichment(self):
        self.assertFalse(needs_reenrichment(_row()))

    def test_deadline_confidence_none_triggers_reenrichment(self):
        self.assertTrue(needs_reenrichment(_row(deadline_confidence="none")))

    def test_deadline_confidence_null_triggers_reenrichment(self):
        self.assertTrue(needs_reenrichment(_row(deadline_confidence=None)))

    def test_relevance_score_null_triggers_reenrichment(self):
        self.assertTrue(needs_reenrichment(_row(relevance_score=None)))

    def test_enriched_zero_triggers_reenrichment(self):
        self.assertTrue(needs_reenrichment(_row(enriched=0)))

    def test_missing_last_enriched_triggers_reenrichment(self):
        self.assertTrue(needs_reenrichment(_row(last_enriched=None)))

    def test_stale_last_enriched_triggers_reenrichment(self):
        old = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        self.assertTrue(needs_reenrichment(_row(last_enriched=old)))

    def test_recent_last_enriched_within_14_days_does_not_trigger(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.assertFalse(needs_reenrichment(_row(last_enriched=recent)))

    def test_unparseable_last_enriched_triggers_reenrichment(self):
        self.assertTrue(needs_reenrichment(_row(last_enriched="not-a-date")))


if __name__ == "__main__":
    unittest.main()
