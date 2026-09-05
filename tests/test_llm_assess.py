import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import llm_assess, llm_enrich
from lib.extract import Document


class _FakeEligibility:
    def __init__(self, **kwargs):
        defaults = dict(
            requires_incorporation=None, requires_faculty_sponsor=None, requires_us_person=None,
            min_age=None, max_age=None, entry_fee_usd=None, max_team_size=None,
            requires_enrollment=None, equity_required=None,
        )
        defaults.update(kwargs)
        self.__dict__.update(defaults)

    def model_dump(self):
        return dict(self.__dict__)


class _FakeAssessment:
    def __init__(self, robotics_relevant=True, relevance_evidence=None, eligibility=None, eligibility_evidence=None):
        self.robotics_relevant = robotics_relevant
        self.relevance_evidence = relevance_evidence
        self.eligibility = eligibility or _FakeEligibility()
        self.eligibility_evidence = eligibility_evidence


def _all_none_enrichment():
    e = {"relevance_eligible": True}
    for f in llm_assess.ELIGIBILITY_FIELDS:
        e[f] = None
        e[f"{f}_confidence"] = "none"
    return e


class TestAssessItem(unittest.TestCase):
    def setUp(self):
        self.budget = llm_enrich.LLMCallBudget(5)
        self.doc = Document(text="This robot contest is open to US citizens only. No entry fee.", structured=[])

    @patch.dict(os.environ, {}, clear=True)
    @patch("lib.llm_assess._call_assessment_llm")
    def test_no_api_key_skips_call_entirely(self, mock_call):
        result = llm_assess.assess_item(self.doc, {}, _all_none_enrichment(), self.budget)
        mock_call.assert_not_called()
        self.assertEqual(result, {})
        self.assertEqual(self.budget.remaining, 5)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_assess._call_assessment_llm")
    def test_budget_exhausted_skips_call(self, mock_call):
        exhausted = llm_enrich.LLMCallBudget(0)
        result = llm_assess.assess_item(self.doc, {}, _all_none_enrichment(), exhausted)
        mock_call.assert_not_called()
        self.assertEqual(result, {})

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_assess._call_assessment_llm")
    def test_api_failure_returns_empty_dict(self, mock_call):
        mock_call.return_value = None
        result = llm_assess.assess_item(self.doc, {}, _all_none_enrichment(), self.budget)
        self.assertEqual(result, {})
        self.assertEqual(self.budget.remaining, 4)  # attempt still consumed

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_assess._call_assessment_llm")
    def test_relevance_claim_with_valid_evidence_is_applied(self, mock_call):
        mock_call.return_value = _FakeAssessment(
            robotics_relevant=True, relevance_evidence="This robot contest is open"
        )
        result = llm_assess.assess_item(self.doc, {}, _all_none_enrichment(), self.budget)
        self.assertTrue(result["llm_robotics_relevant"])
        self.assertEqual(result["llm_relevance_evidence"], "This robot contest is open")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_assess._call_assessment_llm")
    def test_relevance_evidence_not_verbatim_drops_whole_assessment(self, mock_call):
        mock_call.return_value = _FakeAssessment(
            robotics_relevant=True,
            relevance_evidence="this quote does not appear anywhere in the text",
            eligibility=_FakeEligibility(requires_us_person=True),
            eligibility_evidence="US citizens only",
        )
        result = llm_assess.assess_item(self.doc, {}, _all_none_enrichment(), self.budget)
        self.assertEqual(result, {})

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_assess._call_assessment_llm")
    def test_eligibility_evidence_not_verbatim_drops_whole_assessment(self, mock_call):
        mock_call.return_value = _FakeAssessment(
            eligibility=_FakeEligibility(requires_us_person=True),
            eligibility_evidence="a quote that is not in the page text",
        )
        result = llm_assess.assess_item(self.doc, {}, _all_none_enrichment(), self.budget)
        self.assertEqual(result, {})

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_assess._call_assessment_llm")
    def test_eligibility_fields_filled_with_llm_confidence(self, mock_call):
        mock_call.return_value = _FakeAssessment(
            eligibility=_FakeEligibility(requires_us_person=True, entry_fee_usd=0.0),
            eligibility_evidence="US citizens only",
        )
        result = llm_assess.assess_item(self.doc, {}, _all_none_enrichment(), self.budget)
        self.assertTrue(result["requires_us_person"])
        self.assertEqual(result["requires_us_person_confidence"], "llm")
        self.assertEqual(result["entry_fee_usd"], 0.0)
        self.assertEqual(result["entry_fee_usd_confidence"], "llm")
        self.assertEqual(result["eligibility_llm_evidence"], "US citizens only")
        # Fields the model didn't claim anything about stay absent.
        self.assertNotIn("max_age", result)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_assess._call_assessment_llm")
    def test_never_overwrites_a_field_already_resolved_deterministically(self, mock_call):
        mock_call.return_value = _FakeAssessment(
            eligibility=_FakeEligibility(requires_us_person=False),  # contradicts the deterministic fact
            eligibility_evidence="US citizens only",
        )
        enrichment = _all_none_enrichment()
        enrichment["requires_us_person"] = True
        enrichment["requires_us_person_confidence"] = "explicit"
        result = llm_assess.assess_item(self.doc, {}, enrichment, self.budget)
        # The already-resolved field must not appear in the update at all.
        self.assertNotIn("requires_us_person", result)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_assess._call_assessment_llm")
    def test_no_evidence_at_all_yields_no_claims(self, mock_call):
        mock_call.return_value = _FakeAssessment(
            robotics_relevant=True, relevance_evidence=None,
            eligibility=_FakeEligibility(requires_us_person=True), eligibility_evidence=None,
        )
        result = llm_assess.assess_item(self.doc, {}, _all_none_enrichment(), self.budget)
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
