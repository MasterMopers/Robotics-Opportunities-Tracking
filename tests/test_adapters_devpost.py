import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.devpost_api import map_hackathon, parse_submission_deadline, fetch

# A captured (real-shaped, values taken from the Phase 0b live probe of
# https://devpost.com/api/hackathons?search=robotics&status[]=open --
# see docs/PROBES.md) JSON response fixture.
CAPTURED_HACKATHON = {
    "id": 29884,
    "title": "VoltHacks",
    "displayed_location": {"icon": "globe", "location": "Online"},
    "open_state": "open",
    "url": "https://volthacks.devpost.com/",
    "submission_period_dates": "May 22 - Sep 13, 2026",
    "themes": [{"id": 7, "name": "IoT"}, {"id": 6, "name": "Machine Learning/AI"}],
    "prize_amount": "$<span data-currency-value>35,785</span>",
    "registrations_count": 2002,
    "organization_name": "Dialogate",
}


class TestParseSubmissionDeadline(unittest.TestCase):
    def test_end_of_range_parsed(self):
        date, conf = parse_submission_deadline("May 22 - Sep 13, 2026")
        self.assertEqual(date, "2026-09-13")
        self.assertEqual(conf, "explicit")

    def test_shared_month_range_borrows_month(self):
        date, conf = parse_submission_deadline("Sep 10 - 13, 2026")
        self.assertEqual(date, "2026-09-13")
        self.assertEqual(conf, "explicit")

    def test_empty_string_is_none(self):
        date, conf = parse_submission_deadline("")
        self.assertIsNone(date)
        self.assertEqual(conf, "none")

    def test_unparseable_is_none(self):
        date, conf = parse_submission_deadline("Rolling")
        self.assertIsNone(date)
        self.assertEqual(conf, "none")


class TestMapHackathon(unittest.TestCase):
    def test_maps_captured_response_to_right_columns(self):
        item = map_hackathon(CAPTURED_HACKATHON)
        self.assertEqual(item["title"], "VoltHacks")
        self.assertEqual(item["url"], "https://volthacks.devpost.com/")
        self.assertIn("IoT", item["snippet"])

        prefilled = item["prefilled"]
        self.assertEqual(prefilled["deadline_date"], "2026-09-13")
        self.assertEqual(prefilled["deadline_confidence"], "explicit")
        # "Online" displayed_location maps to Remote format, no venue.
        self.assertIsNone(prefilled["location"])
        self.assertEqual(prefilled["location_format"], "Remote")
        self.assertEqual(prefilled["participants_count"], 2002)
        self.assertEqual(prefilled["participants_confidence"], "explicit")
        # prize_amount's <span data-currency-value> wrapper is stripped.
        self.assertEqual(prefilled["money_raw"], "$35,785")

    def test_in_person_location_mapped_explicit(self):
        h = dict(CAPTURED_HACKATHON, displayed_location={"location": "Austin, TX"})
        item = map_hackathon(h)
        self.assertEqual(item["prefilled"]["location"], "Austin, TX")
        self.assertEqual(item["prefilled"]["location_format"], "In-person")
        self.assertEqual(item["prefilled"]["location_confidence"], "explicit")

    def test_no_money_amount_is_none(self):
        h = dict(CAPTURED_HACKATHON, prize_amount=None)
        item = map_hackathon(h)
        self.assertIsNone(item["prefilled"]["money_raw"])


class TestFetchFiltersAndDedupes(unittest.TestCase):
    def test_skips_non_open_non_upcoming_and_dedupes_across_queries(self):
        closed = dict(CAPTURED_HACKATHON, open_state="ended")

        class FakeResp:
            def json(self):
                return {"hackathons": [CAPTURED_HACKATHON, closed]}

        with patch("adapters.devpost_api.get", return_value=FakeResp()):
            items = fetch({"id": "devpost"})

        # Every (term, status) query combo returns the same fixture data in
        # this mock -- the open one should be deduped down to a single
        # item by URL, and the "ended" one must never appear.
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "VoltHacks")


if __name__ == "__main__":
    unittest.main()
