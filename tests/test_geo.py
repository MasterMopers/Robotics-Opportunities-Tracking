import os
import sys
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.geo import classify_country

RULES = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "rules.yaml")))


class TestClassifyCountry(unittest.TestCase):
    def test_explicit_us_state_name(self):
        self.assertEqual(classify_country("Austin, Texas", RULES), "US")

    def test_us_state_abbrev_with_comma(self):
        self.assertEqual(classify_country("Austin, TX", RULES), "US")

    def test_explicit_usa_token(self):
        self.assertEqual(classify_country("Some City, USA", RULES), "US")

    def test_non_us_country_name(self):
        self.assertEqual(classify_country("London, United Kingdom", RULES), "non-US")

    def test_non_us_region_name(self):
        self.assertEqual(classify_country("Bengaluru, Karnataka", RULES), "non-US")

    def test_bengaluru_hackathon_row_excluded(self):
        # The exact real-world row that prompted this filter.
        self.assertEqual(classify_country("Bengaluru, Karnataka", RULES), "non-US")

    def test_empty_string_is_unknown_not_excluded(self):
        self.assertEqual(classify_country("", RULES), "unknown")

    def test_none_is_unknown_not_excluded(self):
        self.assertEqual(classify_country(None, RULES), "unknown")

    def test_ambiguous_text_is_unknown_not_assumed_us(self):
        # No US or non-US marker present -- must not default to "US".
        self.assertEqual(classify_country("Main Street Community Center", RULES), "unknown")

    def test_state_abbrev_false_positive_guard(self):
        # "IN" (Indiana) must not match ordinary text containing the word
        # "in" without a preceding comma -- this is exactly why the
        # abbreviation match requires ", XX".
        self.assertEqual(classify_country("Held in a large venue", RULES), "unknown")

    def test_non_us_marker_wins_over_coincidental_abbrev(self):
        # Guards the check-order: a non-US country name must not be
        # overridden by an incidental US-looking token.
        self.assertEqual(classify_country("Toronto, Ontario", RULES), "non-US")


if __name__ == "__main__":
    unittest.main()
