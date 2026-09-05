import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from lib.enrich import extract_deadline, extract_location, extract_participants
from lib.extract import extract_document

RULES = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules.yaml")))


def doc(html):
    return extract_document(html)


class TestExtractDeadline(unittest.TestCase):
    def test_close_on_phrasing(self):
        # Regression: HopHacks' real page text is "Applications close on
        # September 1, 2026" -- the original pattern required the date
        # immediately after "close(s)", so the word "on" alone broke the
        # match (confirmed against the live page, 2026-09).
        html = "<html><body><p>Applications close on September 1, 2026.</p></body></html>"
        date, conf = extract_deadline(doc(html), RULES)
        self.assertEqual(date, "2026-09-01")
        self.assertEqual(conf, "explicit")

    def test_original_phrasing_without_preposition_still_matches(self):
        html = "<html><body><p>Submissions due March 3, 2027.</p></body></html>"
        date, conf = extract_deadline(doc(html), RULES)
        self.assertEqual(date, "2027-03-03")
        self.assertEqual(conf, "explicit")

    def test_jsonld_application_deadline(self):
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type": "Event", "name": "Some Hackathon", "applicationDeadline": "2026-10-15"}'
            "</script></head><body></body></html>"
        )
        date, conf = extract_deadline(doc(html), RULES)
        self.assertEqual(date, "2026-10-15")
        self.assertEqual(conf, "explicit")

    def test_jsonld_start_date_is_not_treated_as_deadline(self):
        # Regression: SteelHacks' real JSON-LD has startDate/endDate only,
        # no application-deadline field at all (confirmed against the live
        # page, 2026-09) -- an event's own date must never be mislabeled as
        # when applications close.
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type": "Event", "name": "SteelHacks XIII", "startDate": "2026-09-19", '
            '"endDate": "2026-09-20"}'
            "</script></head><body></body></html>"
        )
        date, conf = extract_deadline(doc(html), RULES)
        self.assertIsNone(date)
        self.assertEqual(conf, "none")

    def test_no_deadline_stated_is_none(self):
        html = "<html><body><p>Come build something great with us this year!</p></body></html>"
        date, conf = extract_deadline(doc(html), RULES)
        self.assertIsNone(date)
        self.assertEqual(conf, "none")

    def test_relative_deadline_still_works(self):
        html = "<html><body><p>Hurry, closes in 5 days!</p></body></html>"
        date, conf = extract_deadline(doc(html), RULES, today=__import__("datetime").date(2026, 1, 1))
        self.assertEqual(date, "2026-01-06")
        self.assertEqual(conf, "relative")

    def test_malformed_jsonld_is_skipped_not_raised(self):
        html = (
            '<html><head><script type="application/ld+json">'
            "{not valid json at all"
            "</script></head><body><p>Applications close on September 1, 2026.</p></body></html>"
        )
        date, conf = extract_deadline(doc(html), RULES)
        self.assertEqual(date, "2026-09-01")
        self.assertEqual(conf, "explicit")


class TestExtractLocation(unittest.TestCase):
    def test_devpost_jsonld_event_address(self):
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type": "Event", "location": {"@type": "Place", "address": '
            '{"@type": "PostalAddress", "addressLocality": "Bengaluru", "addressRegion": "Karnataka"}}}'
            "</script></head><body></body></html>"
        )
        location, fmt, conf = extract_location(doc(html), RULES)
        self.assertEqual(location, "Bengaluru, Karnataka")
        self.assertEqual(fmt, "In-person")
        self.assertEqual(conf, "explicit")

    def test_ignores_organization_address_without_event_type(self):
        # Regression: Hackster's contest pages carry Hackster's own HQ
        # address in an Organization JSON-LD block with no Event block at
        # all -- must not be mistaken for the contest's own location.
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type": "Organization", "name": "Hackster.io", "address": '
            '{"@type": "PostalAddress", "addressLocality": "San Jose", "addressRegion": "CA"}}'
            "</script></head><body></body></html>"
        )
        location, fmt, conf = extract_location(doc(html), RULES)
        self.assertIsNone(location)
        self.assertEqual(fmt, "Unknown")
        self.assertEqual(conf, "none")

    def test_mlh_hero_location_class(self):
        html = '<html><body><p class="lv-hero-location">Houston, Texas</p></body></html>'
        location, fmt, conf = extract_location(doc(html), RULES)
        self.assertEqual(location, "Houston, Texas")
        self.assertEqual(fmt, "In-person")

    def test_rejects_html_attribute_noise_from_bare_venue_match(self):
        # Regression: a naive 'venue' keyword match landed inside
        # aria-labelledby="schedule-venue-title"> and was captured as if it
        # were a real venue name. As of Phase 1 this is structurally
        # impossible: doc.text is BeautifulSoup.get_text() output, which
        # never includes attribute values at all -- there is no junk-token
        # filter needed any more because the noise can't reach this
        # function in the first place.
        html = '<html><body><div aria-labelledby="schedule-venue-title">Schedule</div></body></html>'
        location, fmt, conf = extract_location(doc(html), RULES)
        self.assertIsNone(location)

    def test_trims_trailing_time_clause(self):
        html = "<html><body><p>The event will be held at Johns Hopkins University in the fall.</p></body></html>"
        location, fmt, conf = extract_location(doc(html), RULES)
        self.assertEqual(location, "Johns Hopkins University")

    def test_rejects_time_only_capture(self):
        html = "<html><body><p>Doors open, held at 530pm on Aug 21st for the kickoff.</p></body></html>"
        location, fmt, conf = extract_location(doc(html), RULES)
        self.assertIsNone(location)

    def test_remote_phrase_without_city_is_inferred_not_explicit(self):
        html = "<html><body><p>This is a fully remote fellowship, apply from anywhere.</p></body></html>"
        location, fmt, conf = extract_location(doc(html), RULES)
        self.assertIsNone(location)
        self.assertEqual(fmt, "Remote")
        self.assertEqual(conf, "inferred")

    def test_no_signal_is_unknown(self):
        html = "<html><body><p>Build something great and submit your project.</p></body></html>"
        location, fmt, conf = extract_location(doc(html), RULES)
        self.assertIsNone(location)
        self.assertEqual(fmt, "Unknown")
        self.assertEqual(conf, "none")

    def test_hybrid_when_both_in_person_and_remote_phrases_present(self):
        html = "<html><body><p>This is a hybrid event: join in-person or virtual event online.</p></body></html>"
        location, fmt, conf = extract_location(doc(html), RULES)
        self.assertEqual(fmt, "Hybrid")


class TestExtractParticipants(unittest.TestCase):
    def test_devpost_nav_count(self):
        html = "<html><body><span>Participants (9)</span></body></html>"
        count, conf = extract_participants(doc(html), RULES)
        self.assertEqual(count, 9)
        self.assertEqual(conf, "explicit")

    def test_hackster_stat_count(self):
        # Real page shape: two adjacent <span> elements, label then count --
        # collapses to "Participants 141" once tags are stripped.
        html = '<html><body><div><span>Participants</span><span class="x">141</span></div></body></html>'
        count, conf = extract_participants(doc(html), RULES)
        self.assertEqual(count, 141)

    def test_comma_thousands(self):
        html = "<html><body><p>1,200 applicants competed this year</p></body></html>"
        count, conf = extract_participants(doc(html), RULES)
        self.assertEqual(count, 1200)

    def test_no_number_stated_is_none(self):
        html = "<html><body><p>Great turnout every year!</p></body></html>"
        count, conf = extract_participants(doc(html), RULES)
        self.assertIsNone(count)
        self.assertEqual(conf, "none")


if __name__ == "__main__":
    unittest.main()
