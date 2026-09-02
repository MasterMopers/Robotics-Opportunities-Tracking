"""Opt-in, fallback-only LLM enrichment for location/participants fields.

Never called unless OPENAI_API_KEY is set. Never touches deadline, money,
team_size, matched_signals, scores, or reject -- those feed classify_item()
and classification stays 100% deterministic (see monitor.py: classify_item()
is called before this module ever runs). This module only ever fills
location/location_format/participants_count, and only for the specific
fields lib.enrich.enrich_item() left unresolved (confidence == "none"),
tagging anything it fills with confidence "llm".

Regex always runs first, for free. This is a fallback for what regex
couldn't find on the page -- not a replacement for it.
"""

import os

MAX_LLM_CALLS_PER_RUN = 25          # weekly discovery run
MAX_LLM_CALLS_PER_BACKFILL = 200    # one-off --backfill catch-up pass
MAX_LLM_INPUT_CHARS = 8000          # bound tokens/cost per call; page_text is raw HTML

MODEL = "gpt-5-nano"

SYSTEM_PROMPT = """You are a strict, literal data-extraction assistant for a robotics \
opportunity tracker. You will be given raw text scraped from a contest or grant's own \
webpage (it may include leftover HTML tags or JSON-LD markup).

Extract ONLY facts the page explicitly states. Rules:

1. location: the specific city, venue, or place name the page states as where the \
event/program itself takes place. Output null if no specific place is stated. Do not \
guess a location from a university name, sponsor name, or organizer's headquarters \
unless the page states that is where the event happens.
2. location_format: one of "In-person", "Remote", "Hybrid". Output null unless the \
page's own wording clearly states or strongly implies the format. Do not set this to \
"In-person" just because a location was found -- only set it when the format itself is \
stated.
3. participants_count: an integer count of participants, applicants, teams, or \
attendees the page explicitly states (e.g. "141 participants", "1,200 applicants"). \
Output null if no such number is stated. Never estimate, round, or derive this from an \
unrelated number (prize amounts, team-size limits, dates, years, dollar figures).

Never infer, estimate, or guess beyond what is explicitly written in the provided text. \
When in doubt, output null. Do not use outside/background knowledge about the \
organization, school, or event -- rely solely on the text provided."""


class LLMCallBudget:
    """Shared, mutable per-run cap on total LLM calls. Decrements on attempt,
    not success, so it also bounds a run that's failing repeatedly."""

    def __init__(self, max_calls: int):
        self.remaining = max_calls

    def try_consume(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def is_enabled() -> bool:
    """Explicit, obvious gate. No try/except around a missing key -- either
    it's set or the whole LLM path is skipped, byte-for-byte identical to
    behavior before this module existed."""
    return bool(os.environ.get("OPENAI_API_KEY"))


def _call_llm(text: str):
    """Single attempt, fixed timeout, broad except -> None on any failure
    (matches adapters/_http.py's convention: no retries anywhere in this
    codebase). Imports `openai`/`pydantic` lazily so that `import
    lib.llm_enrich` never fails even if the package isn't installed in an
    environment where OPENAI_API_KEY also isn't set."""
    try:
        from openai import OpenAI
        from pydantic import BaseModel
        from typing import Optional, Literal
    except ImportError:
        return None

    class OpportunityFields(BaseModel):
        location: Optional[str]
        location_format: Optional[Literal["In-person", "Remote", "Hybrid"]]
        participants_count: Optional[int]

    try:
        client = OpenAI(timeout=20)
        completion = client.chat.completions.parse(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text[:MAX_LLM_INPUT_CHARS]},
            ],
            response_format=OpportunityFields,
        )
        return completion.choices[0].message.parsed
    except Exception:
        return None


def apply_llm_fallback(enrichment: dict, text: str, budget: LLMCallBudget) -> dict:
    """Fallback-only: returns enrichment unchanged unless regex left a gap,
    OPENAI_API_KEY is set, and the budget allows one more attempt. On any
    failure/timeout/exhausted-budget/disabled-key, returns enrichment
    unchanged -- same as today's behavior. Only writes back the field(s)
    regex actually left as "none", even if the model's response also
    includes a value for a field regex already resolved."""
    needs_location = enrichment["location_confidence"] == "none"
    needs_participants = enrichment["participants_confidence"] == "none"
    if not (needs_location or needs_participants):
        return enrichment
    if not is_enabled():
        return enrichment
    if not budget.try_consume():
        return enrichment

    result = _call_llm(text)
    if result is None:
        return enrichment

    filled = dict(enrichment)
    if needs_location and result.location:
        filled["location"] = result.location
        filled["location_format"] = result.location_format or enrichment["location_format"]
        filled["location_confidence"] = "llm"
    if needs_participants and result.participants_count is not None:
        filled["participants_count"] = result.participants_count
        filled["participants_confidence"] = "llm"
    return filled
