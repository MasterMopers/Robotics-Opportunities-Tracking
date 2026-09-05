"""Opt-in, fallback-only LLM enrichment for location/participants/deadline
fields.

Never called unless OPENAI_API_KEY is set. Never touches money, team_size,
matched_signals, scores, or reject -- those feed classify_item() and
classification stays 100% deterministic (see monitor.py: classify_item() is
called before this module ever runs). This module only ever fills
location/location_format/participants_count/deadline_date, and only for the
specific fields lib.enrich.enrich_item() left unresolved (confidence ==
"none"), tagging anything it fills with confidence "llm".

Regex/JSON-LD always runs first, for free. This is a fallback for what that
couldn't find on the page -- not a replacement for it. Deadline is the one
field where this fallback is expected to fire on most items, not
occasionally: regex/JSON-LD alone resolve very few real deadlines (most
pages state one in a phrasing neither anticipates), so this is a deliberate,
accepted shift from "rare fallback" to "routine assist" for that field
specifically -- still hard-capped by MAX_LLM_CALLS_PER_RUN either way.
"""

import os

MAX_LLM_CALLS_PER_RUN = 25          # weekly discovery run
MAX_LLM_CALLS_PER_BACKFILL = 250    # one-off --backfill catch-up pass
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
4. deadline: the specific date applications/entries/submissions must be in by -- an \
APPLICATION or ENTRY deadline specifically. Output the date as plain text exactly as \
the page states it (e.g. "September 1, 2026"). Output null if the page does not state \
an application deadline. Do NOT output an event's start date, end date, or the date the \
event itself takes place as if it were a deadline -- those are different things. Many \
pages state only when the event happens and never say when applications close; in that \
case output null rather than substituting the event date.

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
        deadline: Optional[str]

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


def _parse_llm_date(raw):
    """Parse the LLM's raw deadline text the same way the regex path parses
    an explicit-pattern match -- if it doesn't parse, there's no deadline,
    never a stored guess. Lazy import: dateutil is a hard dependency of
    lib/enrich.py already, but this module otherwise has zero imports at
    module load time by design (see is_enabled()/module docstring)."""
    if not raw:
        return None
    from dateutil import parser as dateutil_parser

    try:
        return dateutil_parser.parse(raw, fuzzy=True, default=None).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def apply_llm_fallback(enrichment: dict, text: str, budget: LLMCallBudget) -> dict:
    """Fallback-only: returns enrichment unchanged unless regex/JSON-LD left
    a gap, OPENAI_API_KEY is set, and the budget allows one more attempt. On
    any failure/timeout/exhausted-budget/disabled-key, returns enrichment
    unchanged -- same as today's behavior. Only writes back the field(s)
    regex actually left as "none", even if the model's response also
    includes a value for a field regex already resolved.

    Deadline is expected to need this fallback on most items (regex/JSON-LD
    resolve very few real deadlines), unlike location/participants where it
    fires occasionally -- see the module docstring."""
    needs_location = enrichment["location_confidence"] == "none"
    needs_participants = enrichment["participants_confidence"] == "none"
    needs_deadline = enrichment["deadline_confidence"] == "none"
    if not (needs_location or needs_participants or needs_deadline):
        return enrichment
    if not is_enabled():
        return enrichment
    if not budget.try_consume():
        return enrichment

    result = _call_llm(text)
    if result is None:
        return enrichment

    filled = dict(enrichment)
    if needs_location and (result.location or result.location_format):
        # location and location_format are independently fillable -- a
        # remote/hybrid event with no stated city must still get its format
        # (previously both were gated on result.location being truthy, which
        # silently dropped the format for any event with no named venue).
        if result.location:
            filled["location"] = result.location
        if result.location_format:
            filled["location_format"] = result.location_format
        filled["location_confidence"] = "llm"
    if needs_participants and result.participants_count is not None:
        filled["participants_count"] = result.participants_count
        filled["participants_confidence"] = "llm"
    if needs_deadline:
        parsed = _parse_llm_date(result.deadline)
        if parsed:
            filled["deadline_date"] = parsed
            filled["deadline_confidence"] = "llm"
    return filled
