import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import llm_enrich


class _FakeParsed:
    def __init__(self, location=None, location_format=None, participants_count=None, deadline=None):
        self.location = location
        self.location_format = location_format
        self.participants_count = participants_count
        self.deadline = deadline


def _enrichment(loc_conf="none", part_conf="none", location=None, fmt="Unknown", count=None,
                 deadline_conf="none", deadline_date=None):
    return {
        "location": location, "location_format": fmt, "location_confidence": loc_conf,
        "participants_count": count, "participants_confidence": part_conf,
        "deadline_date": deadline_date, "deadline_confidence": deadline_conf,
    }


class TestApplyLLMFallback(unittest.TestCase):
    def setUp(self):
        self.budget = llm_enrich.LLMCallBudget(5)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_enrich._call_llm")
    def test_regex_resolved_everything_llm_never_called(self, mock_call):
        e = _enrichment(loc_conf="explicit", part_conf="explicit", location="Austin, TX", count=50,
                         deadline_conf="explicit", deadline_date="2026-10-01")
        result = llm_enrich.apply_llm_fallback(e, "text", self.budget)
        mock_call.assert_not_called()
        self.assertEqual(result, e)
        self.assertEqual(self.budget.remaining, 5)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_enrich._call_llm")
    def test_all_three_gaps_filled_with_llm_confidence(self, mock_call):
        mock_call.return_value = _FakeParsed("Boston, MA", "Hybrid", 87, "November 3, 2026")
        result = llm_enrich.apply_llm_fallback(_enrichment(), "text", self.budget)
        mock_call.assert_called_once()
        self.assertEqual(result["location"], "Boston, MA")
        self.assertEqual(result["location_confidence"], "llm")
        self.assertEqual(result["participants_count"], 87)
        self.assertEqual(result["participants_confidence"], "llm")
        self.assertEqual(result["deadline_date"], "2026-11-03")
        self.assertEqual(result["deadline_confidence"], "llm")
        self.assertEqual(self.budget.remaining, 4)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_enrich._call_llm")
    def test_deadline_only_gap_fires_call_even_when_location_and_participants_resolved(self, mock_call):
        # Deadline is expected to need this fallback on most items even when
        # location/participants are already resolved -- confirms the trigger
        # condition is an OR across all three fields, not just location/participants.
        mock_call.return_value = _FakeParsed(deadline="December 1, 2026")
        e = _enrichment(loc_conf="explicit", part_conf="explicit", location="Austin, TX", count=50)
        result = llm_enrich.apply_llm_fallback(e, "text", self.budget)
        mock_call.assert_called_once()
        self.assertEqual(result["deadline_date"], "2026-12-01")
        self.assertEqual(result["deadline_confidence"], "llm")
        self.assertEqual(result["location"], "Austin, TX")  # untouched

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_enrich._call_llm")
    def test_llm_event_date_confused_for_deadline_is_still_guarded_by_prompt_not_code(self, mock_call):
        # The code layer trusts the model's `deadline` field completely (the
        # event-date-vs-deadline guard lives in the system prompt, not here)
        # -- this test documents that boundary: if the model ever violated
        # its instructions, this function would not catch it. Real assurance
        # comes from the prompt wording plus spot-checking live results.
        mock_call.return_value = _FakeParsed(deadline=None)  # correctly null per prompt instructions
        result = llm_enrich.apply_llm_fallback(_enrichment(), "text", self.budget)
        self.assertIsNone(result["deadline_date"])
        self.assertEqual(result["deadline_confidence"], "none")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_enrich._call_llm")
    def test_unparseable_deadline_text_is_discarded_not_stored(self, mock_call):
        mock_call.return_value = _FakeParsed(deadline="sometime soon, probably")
        result = llm_enrich.apply_llm_fallback(_enrichment(), "text", self.budget)
        self.assertIsNone(result["deadline_date"])
        self.assertEqual(result["deadline_confidence"], "none")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_enrich._call_llm")
    def test_only_missing_field_is_merged_not_the_already_resolved_one(self, mock_call):
        # Regex already found location; the LLM's response also guesses one,
        # but it must be discarded -- only the participants gap gets applied.
        mock_call.return_value = _FakeParsed("Some Other City", "In-person", 12)
        e = _enrichment(loc_conf="explicit", part_conf="none", location="Real City", fmt="In-person")
        result = llm_enrich.apply_llm_fallback(e, "text", self.budget)
        self.assertEqual(result["location"], "Real City")
        self.assertEqual(result["location_confidence"], "explicit")
        self.assertEqual(result["participants_count"], 12)
        self.assertEqual(result["participants_confidence"], "llm")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_enrich._call_llm")
    def test_format_only_fills_even_with_no_named_city(self, mock_call):
        # Regression: a remote/hybrid event with no stated venue must still
        # get its format applied -- previously gated on result.location
        # being truthy, which silently dropped a known format when there
        # was no city to go with it.
        mock_call.return_value = _FakeParsed(location=None, location_format="Remote", participants_count=None)
        result = llm_enrich.apply_llm_fallback(_enrichment(), "text", self.budget)
        self.assertIsNone(result["location"])
        self.assertEqual(result["location_format"], "Remote")
        self.assertEqual(result["location_confidence"], "llm")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_enrich._call_llm")
    def test_api_failure_leaves_fields_unresolved(self, mock_call):
        mock_call.return_value = None  # _call_llm already swallowed the exception
        e = _enrichment()
        result = llm_enrich.apply_llm_fallback(e, "text", self.budget)
        self.assertEqual(result, e)
        self.assertEqual(self.budget.remaining, 4)  # attempt still consumed

    def test_call_llm_swallows_client_exception(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with patch("openai.OpenAI") as mock_openai_cls:
                mock_openai_cls.return_value.chat.completions.parse.side_effect = TimeoutError("boom")
                result = llm_enrich._call_llm("some page text")
        self.assertIsNone(result)

    @patch.dict(os.environ, {}, clear=True)
    @patch("lib.llm_enrich._call_llm")
    def test_no_api_key_skips_llm_path_entirely(self, mock_call):
        result = llm_enrich.apply_llm_fallback(_enrichment(), "text", self.budget)
        mock_call.assert_not_called()
        self.assertEqual(result["location_confidence"], "none")
        self.assertEqual(self.budget.remaining, 5)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    @patch("lib.llm_enrich._call_llm")
    def test_budget_exhausted_skips_call(self, mock_call):
        exhausted = llm_enrich.LLMCallBudget(0)
        result = llm_enrich.apply_llm_fallback(_enrichment(), "text", exhausted)
        mock_call.assert_not_called()
        self.assertEqual(result["location_confidence"], "none")

    def test_module_import_survives_missing_openai_package(self):
        # sys.modules[name] = None forces the next `import name` to raise
        # ImportError -- proves lib.llm_enrich has no top-level `import
        # openai` and that _call_llm's lazy import degrades gracefully.
        import importlib
        with patch.dict(sys.modules, {"openai": None}):
            importlib.reload(llm_enrich)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
                self.assertIsNone(llm_enrich._call_llm("text"))
        importlib.reload(llm_enrich)  # restore normal module state


if __name__ == "__main__":
    unittest.main()
