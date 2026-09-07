import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from lib.eligibility import FIELDS, evaluate, extract_eligibility_fields

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = yaml.safe_load(open(os.path.join(ROOT, "rules.yaml")))
PROFILE = yaml.safe_load(open(os.path.join(ROOT, "profile.yaml")))["profile"]


def _all_unresolved_row():
    row = {}
    for f in FIELDS:
        row[f] = None
        row[f"{f}_confidence"] = "none"
    return row


def _all_clear_row():
    """Every field resolved, and every value passes PROFILE."""
    row = {
        "requires_incorporation": False,
        "requires_faculty_sponsor": False,
        "requires_us_person": True,
        "min_age": 18,
        "max_age": None,
        "entry_fee_usd": 0.0,
        "max_team_size": 4,
        "requires_enrollment": True,
        "equity_required": False,
    }
    for f in FIELDS:
        row[f"{f}_confidence"] = "explicit"
    return row


class TestExtractEligibilityFields(unittest.TestCase):
    def test_requires_incorporation_explicit(self):
        fields = extract_eligibility_fields("Applicants must be incorporated as an LLC.", RULES)
        self.assertTrue(fields["requires_incorporation"])
        self.assertEqual(fields["requires_incorporation_confidence"], "explicit")

    def test_requires_incorporation_negated_not_set(self):
        # The negation-window check catches a negation word immediately
        # before the match (e.g. "no equity funding") -- the same
        # convention lib/enrich.py's reject-rule matching already used.
        fields = extract_eligibility_fields("No incorporated entity is required to apply.", RULES)
        self.assertIsNone(fields["requires_incorporation"])
        self.assertEqual(fields["requires_incorporation_confidence"], "none")

    def test_requires_faculty_sponsor_explicit(self):
        fields = extract_eligibility_fields("You must have a faculty sponsor to enter.", RULES)
        self.assertTrue(fields["requires_faculty_sponsor"])
        self.assertEqual(fields["requires_faculty_sponsor_confidence"], "explicit")

    def test_equity_required_explicit(self):
        fields = extract_eligibility_fields("This program takes equity in your company.", RULES)
        self.assertTrue(fields["equity_required"])
        self.assertEqual(fields["equity_required_confidence"], "explicit")

    def test_equity_negated_not_set(self):
        fields = extract_eligibility_fields("No equity required, ever.", RULES)
        self.assertIsNone(fields["equity_required"])
        self.assertEqual(fields["equity_required_confidence"], "none")

    def test_requires_us_person_explicit_true(self):
        fields = extract_eligibility_fields("US citizens only may enter this contest.", RULES)
        self.assertTrue(fields["requires_us_person"])
        self.assertEqual(fields["requires_us_person_confidence"], "explicit")

    def test_requires_us_person_explicit_false(self):
        fields = extract_eligibility_fields("This contest is open to applicants worldwide.", RULES)
        self.assertFalse(fields["requires_us_person"])
        self.assertEqual(fields["requires_us_person_confidence"], "explicit")

    def test_requires_us_person_unstated_is_none(self):
        fields = extract_eligibility_fields("Build something cool and submit it.", RULES)
        self.assertIsNone(fields["requires_us_person"])
        self.assertEqual(fields["requires_us_person_confidence"], "none")

    def test_max_age_from_k12(self):
        fields = extract_eligibility_fields("Open to students in grades K-12.", RULES)
        self.assertEqual(fields["max_age"], 18)
        self.assertEqual(fields["max_age_confidence"], "explicit")

    def test_age_range_sets_both_bounds(self):
        fields = extract_eligibility_fields("This program is open to ages 13-18.", RULES)
        self.assertEqual(fields["min_age"], 13)
        self.assertEqual(fields["max_age"], 18)
        self.assertEqual(fields["min_age_confidence"], "explicit")
        self.assertEqual(fields["max_age_confidence"], "explicit")

    def test_min_age_numeric(self):
        fields = extract_eligibility_fields("Must be at least 18 years old to enter.", RULES)
        self.assertEqual(fields["min_age"], 18)
        self.assertEqual(fields["min_age_confidence"], "explicit")

    def test_entry_fee_free(self):
        fields = extract_eligibility_fields("There is no entry fee for this hackathon.", RULES)
        self.assertEqual(fields["entry_fee_usd"], 0.0)
        self.assertEqual(fields["entry_fee_usd_confidence"], "explicit")

    def test_entry_fee_numeric(self):
        fields = extract_eligibility_fields("There is an entry fee of $25 to apply.", RULES)
        self.assertEqual(fields["entry_fee_usd"], 25.0)
        self.assertEqual(fields["entry_fee_usd_confidence"], "explicit")

    def test_entry_fee_unstated_is_none(self):
        fields = extract_eligibility_fields("Build something cool and submit it.", RULES)
        self.assertIsNone(fields["entry_fee_usd"])
        self.assertEqual(fields["entry_fee_usd_confidence"], "none")

    def test_max_team_size_numeric(self):
        fields = extract_eligibility_fields("Teams of up to 5 members may enter.", RULES)
        self.assertEqual(fields["max_team_size"], 5)
        self.assertEqual(fields["max_team_size_confidence"], "explicit")

    def test_max_team_size_solo_only(self):
        fields = extract_eligibility_fields("This is for individual entries only.", RULES)
        self.assertEqual(fields["max_team_size"], 1)
        self.assertEqual(fields["max_team_size_confidence"], "explicit")

    def test_requires_enrollment_explicit(self):
        fields = extract_eligibility_fields("Applicants must be currently enrolled students.", RULES)
        self.assertTrue(fields["requires_enrollment"])
        self.assertEqual(fields["requires_enrollment_confidence"], "explicit")


class TestEvaluate(unittest.TestCase):
    def test_all_clear_is_eligible(self):
        verdict, reason = evaluate(_all_clear_row(), PROFILE)
        self.assertEqual(verdict, "eligible")
        self.assertIsNone(reason)

    def test_all_unresolved_is_unknown(self):
        verdict, reason = evaluate(_all_unresolved_row(), PROFILE)
        self.assertEqual(verdict, "unknown")
        self.assertIn(reason, FIELDS)

    def test_requires_incorporation_fails_profile(self):
        row = _all_clear_row()
        row["requires_incorporation"] = True
        verdict, reason = evaluate(row, PROFILE)
        self.assertEqual((verdict, reason), ("ineligible", "requires_incorporation"))

    def test_requires_faculty_sponsor_fails_profile(self):
        row = _all_clear_row()
        row["requires_faculty_sponsor"] = True
        verdict, reason = evaluate(row, PROFILE)
        self.assertEqual((verdict, reason), ("ineligible", "requires_faculty_sponsor"))

    def test_requires_us_person_fails_profile(self):
        row = _all_clear_row()
        row["requires_us_person"] = True
        profile = dict(PROFILE, us_person=False)
        verdict, reason = evaluate(row, profile)
        self.assertEqual((verdict, reason), ("ineligible", "requires_us_person"))

    def test_min_age_fails_profile(self):
        row = _all_clear_row()
        row["min_age"] = 25  # profile age is 20
        verdict, reason = evaluate(row, PROFILE)
        self.assertEqual((verdict, reason), ("ineligible", "min_age"))

    def test_max_age_fails_profile(self):
        row = _all_clear_row()
        row["max_age"] = 18  # profile age is 20
        verdict, reason = evaluate(row, PROFILE)
        self.assertEqual((verdict, reason), ("ineligible", "max_age"))

    def test_entry_fee_fails_profile(self):
        row = _all_clear_row()
        row["entry_fee_usd"] = 25.0  # profile will_pay_entry_fee is False
        verdict, reason = evaluate(row, PROFILE)
        self.assertEqual((verdict, reason), ("ineligible", "entry_fee_usd"))

    def test_entry_fee_zero_does_not_fail(self):
        row = _all_clear_row()
        row["entry_fee_usd"] = 0.0
        verdict, reason = evaluate(row, PROFILE)
        self.assertEqual(verdict, "eligible")

    def test_max_team_size_fails_profile(self):
        row = _all_clear_row()
        row["max_team_size"] = 1
        profile = dict(PROFILE, team_size=2)
        verdict, reason = evaluate(row, profile)
        self.assertEqual((verdict, reason), ("ineligible", "max_team_size"))

    def test_requires_enrollment_fails_profile(self):
        row = _all_clear_row()
        row["requires_enrollment"] = True
        profile = dict(PROFILE, currently_enrolled=False)
        verdict, reason = evaluate(row, profile)
        self.assertEqual((verdict, reason), ("ineligible", "requires_enrollment"))

    def test_equity_required_fails_profile(self):
        row = _all_clear_row()
        row["equity_required"] = True
        verdict, reason = evaluate(row, PROFILE)
        self.assertEqual((verdict, reason), ("ineligible", "equity_required"))

    def test_single_unresolved_field_among_resolved_is_unknown(self):
        row = _all_clear_row()
        row["max_age"] = None
        row["max_age_confidence"] = "none"
        verdict, reason = evaluate(row, PROFILE)
        self.assertEqual((verdict, reason), ("unknown", "max_age"))

    def test_hard_fail_wins_over_unrelated_unknown(self):
        row = _all_clear_row()
        row["requires_incorporation"] = True
        row["max_age"] = None
        row["max_age_confidence"] = "none"
        verdict, reason = evaluate(row, PROFILE)
        self.assertEqual((verdict, reason), ("ineligible", "requires_incorporation"))


if __name__ == "__main__":
    unittest.main()
