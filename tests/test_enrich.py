import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from lib.enrich import extract_location, extract_participants

RULES = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules.yaml")))


class TestExtractLocation(unittest.TestCase):
    def test_devpost_jsonld_event_address(self):
        text = (
            '{"@type": "Event", "location": {"@type": "Place", "address": '
            '{"@type": "PostalAddress", "addressLocality": "Bengaluru", "addressRegion": "Karnataka"}}}'
        )
        location, fmt, conf = extract_location(text, RULES)
        self.assertEqual(location, "Bengaluru, Karnataka")
        self.assertEqual(fmt, "In-person")
        self.assertEqual(conf, "explicit")

    def test_ignores_organization_address_without_event_type(self):
        # Regression: Hackster's contest pages carry Hackster's own HQ
        # address in an Organization JSON-LD block with no Event block at
        # all -- must not be mistaken for the contest's own location.
        text = (
            '{"@type": "Organization", "name": "Hackster.io", "address": '
            '{"@type": "PostalAddress", "addressLocality": "San Jose", "addressRegion": "CA"}}'
        )
        location, fmt, conf = extract_location(text, RULES)
        self.assertIsNone(location)
        self.assertEqual(fmt, "Unknown")
        self.assertEqual(conf, "none")

    def test_mlh_hero_location_class(self):
        text = '<p class="lv-hero-location">Houston, Texas</p>'
        location, fmt, conf = extract_location(text, RULES)
        self.assertEqual(location, "Houston, Texas")
        self.assertEqual(fmt, "In-person")

    def test_rejects_html_attribute_noise_from_bare_venue_match(self):
        # Regression: a naive 'venue' keyword match landed inside
        # aria-labelledby="schedule-venue-title"> and was captured as if it
        # were a real venue name. The stricter "Venue:" pattern plus the
        # junk-token filter must not do that.
        text = '<div aria-labelledby="schedule-venue-title">Schedule</div>'
        location, fmt, conf = extract_location(text, RULES)
        self.assertIsNone(location)

    def test_trims_trailing_time_clause(self):
        text = "The event will be held at Johns Hopkins University in the fall."
        location, fmt, conf = extract_location(text, RULES)
        self.assertEqual(location, "Johns Hopkins University")

    def test_rejects_time_only_capture(self):
        text = "Doors open, held at 530pm on Aug 21st for the kickoff."
        location, fmt, conf = extract_location(text, RULES)
        self.assertIsNone(location)

    def test_remote_phrase_without_city_is_inferred_not_explicit(self):
        text = "This is a fully remote fellowship, apply from anywhere."
        location, fmt, conf = extract_location(text, RULES)
        self.assertIsNone(location)
        self.assertEqual(fmt, "Remote")
        self.assertEqual(conf, "inferred")

    def test_no_signal_is_unknown(self):
        text = "Build something great and submit your project."
        location, fmt, conf = extract_location(text, RULES)
        self.assertIsNone(location)
        self.assertEqual(fmt, "Unknown")
        self.assertEqual(conf, "none")

    def test_hybrid_when_both_in_person_and_remote_phrases_present(self):
        text = "This is a hybrid event: join in-person or virtual event online."
        location, fmt, conf = extract_location(text, RULES)
        self.assertEqual(fmt, "Hybrid")


class TestExtractParticipants(unittest.TestCase):
    def test_devpost_nav_count(self):
        count, conf = extract_participants("Participants (9)", RULES)
        self.assertEqual(count, 9)
        self.assertEqual(conf, "explicit")

    def test_hackster_stat_count(self):
        text = 'Participants</span><span class="x">141</span>'
        count, conf = extract_participants(text, RULES)
        self.assertEqual(count, 141)

    def test_comma_thousands(self):
        text = "1,200 applicants competed this year"
        count, conf = extract_participants(text, RULES)
        self.assertEqual(count, 1200)

    def test_no_number_stated_is_none(self):
        count, conf = extract_participants("Great turnout every year!", RULES)
        self.assertIsNone(count)
        self.assertEqual(conf, "none")


if __name__ == "__main__":
    unittest.main()
