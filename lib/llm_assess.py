"""Phase 6: widen the LLM's job without breaking the isolation invariant.

This is a SECOND, SEPARATE call from lib/llm_enrich.py's
apply_llm_fallback -- that one keeps doing exactly what it always did
(fill location/participants/deadline gaps only). This module adds a
distinct assessment call, invoked only for items that have already passed
the deterministic relevance gate (lib/relevance.py's score_relevance ->
relevance_eligible == True). It exists to fill eligibility gaps
(lib/eligibility.py's 8 columns) the deterministic regex extractors left
unresolved, and to attach a labeled robotics-relevance judgment for
ranking/calibration -- never to reclassify anything.

Hard rules, enforced in code, not just prompted for:

1. `assess_item()` writes only to its own columns
   (llm_robotics_relevant / llm_relevance_evidence / the eligibility
   columns, all tagged confidence "llm") -- it is never given the chance
   to touch status, final_class, contest_score, grant_score, or
   relevance_score, because monitor.py never lets it. It cannot promote an
   item that failed the deterministic relevance gate, because monitor.py
   only calls this for items where relevance_eligible is already True.
2. A claim without a verbatim evidence quote is discarded. Every
   `*_evidence` field returned by the model is checked against
   `doc.text` with a literal `in` (substring) check; if either evidence
   string is present but NOT a literal substring, the entire assessment
   is dropped -- not just the offending field. This is what keeps the
   model from inventing an eligibility rule out of nothing: it cannot
   assert a fact without pointing at the exact words on the page that
   state it.
3. It only ever fills an eligibility field the deterministic layer left
   at confidence "none" -- a field lib/eligibility.py's regex extractors
   already resolved (confidence "explicit") is never overwritten here.
"""

from lib.llm_enrich import MAX_LLM_INPUT_CHARS, is_enabled

MODEL = "gpt-5-nano"

ELIGIBILITY_FIELDS = (
    "requires_incorporation",
    "requires_faculty_sponsor",
    "requires_us_person",
    "min_age",
    "max_age",
    "entry_fee_usd",
    "max_team_size",
    "requires_enrollment",
    "equity_required",
)

SYSTEM_PROMPT = """You are a strict, literal assessment assistant for a robotics opportunity \
tracker. You will be given text already confirmed (by a separate, deterministic process) to \
plausibly describe a robotics-relevant contest or grant. Your job has two parts:

1. robotics_relevant: confirm or reject whether this text genuinely describes a contest, \
competition, challenge, or grant a solo undergraduate could enter with a robotics project \
(mechanical, electrical, or software robotics, in any combination). Support your answer with \
relevance_evidence: a verbatim quote from the text. Output null for relevance_evidence if you \
are not confident, but always output a true/false for robotics_relevant.

2. eligibility: extract ONLY eligibility facts the text explicitly states, one field at a time:
   - requires_incorporation: does entering require an LLC/C-corp/incorporated entity?
   - requires_faculty_sponsor: does entering require a faculty advisor, sponsor, or institutional
     nomination?
   - requires_us_person: does entering require US citizenship or residency? Output false only if
     the text explicitly states it is open worldwide/internationally; output null if unstated.
   - min_age / max_age: explicit numeric age bounds the text states.
   - entry_fee_usd: 0 if the text confirms entry is free, a dollar amount if the text states an
     entry/application fee, null if unstated.
   - max_team_size: an explicit maximum team size (1 for "individual/solo entries only").
   - requires_enrollment: does entering require being a currently enrolled student?
   - equity_required: does this program take equity or a SAFE note?

Output null for any eligibility field the text does not explicitly state -- never guess, infer,
or estimate. Support any non-null eligibility field with eligibility_evidence: ONE verbatim quote
from the text covering your strongest piece of evidence (you do not need a separate quote per
field).

Critical rules, do not violate them:
- NEVER infer an eligibility fact from the organizer's identity or reputation (e.g. do not assume
  a university-run program requires enrollment just because a university runs it, unless the text
  itself states an enrollment requirement).
- NEVER treat a sponsor's, organizer's, or judge's location as an eligibility restriction on
  entrants -- a US company sponsoring a globally-open contest does not make requires_us_person
  true, and a program headquartered outside the US does not make it false either, unless the text
  states an entrant eligibility rule directly.
- Every evidence quote you output MUST be an exact, verbatim substring of the provided text --
  not a paraphrase, not a summary. If you cannot find an exact quote, output null for that
  evidence field and null for the corresponding claim(s) it would support.
- When in doubt about any field, output null. A missing fact is always preferable to a guessed
  one."""


def _call_assessment_llm(text: str):
    """Single attempt, broad except -> None on any failure, matching
    lib/llm_enrich.py's _call_llm() convention exactly (no retries
    anywhere in this codebase; lazy imports so this module always imports
    cleanly even without openai/pydantic installed)."""
    try:
        from openai import OpenAI
        from pydantic import BaseModel
        from typing import Optional
    except ImportError:
        return None

    class EligibilityFields(BaseModel):
        requires_incorporation: Optional[bool] = None
        requires_faculty_sponsor: Optional[bool] = None
        requires_us_person: Optional[bool] = None
        min_age: Optional[int] = None
        max_age: Optional[int] = None
        entry_fee_usd: Optional[float] = None
        max_team_size: Optional[int] = None
        requires_enrollment: Optional[bool] = None
        equity_required: Optional[bool] = None

    class Assessment(BaseModel):
        robotics_relevant: bool
        relevance_evidence: Optional[str] = None
        eligibility: EligibilityFields
        eligibility_evidence: Optional[str] = None

    try:
        client = OpenAI(timeout=20)
        completion = client.chat.completions.parse(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text[:MAX_LLM_INPUT_CHARS]},
            ],
            response_format=Assessment,
        )
        return completion.choices[0].message.parsed
    except Exception:
        return None


def assess_item(doc, rules: dict, enrichment: dict, budget) -> dict:
    """Called ONLY for items where enrichment["relevance_eligible"] is
    already True (monitor.py's responsibility to check before calling --
    this function does not re-check it, by design, so the isolation
    invariant lives at the call site as well as here).

    Returns a dict of columns to merge into `enrichment` -- empty if
    disabled, budget-exhausted, the call failed, or the returned evidence
    didn't validate. Never raises. Only ever fills eligibility fields
    `enrichment` left at confidence "none"; never touches
    status/final_class/contest_score/grant_score/relevance_score (those
    aren't in the returned dict at all -- there is no code path back to
    them from here)."""
    if not is_enabled():
        return {}
    if not budget.try_consume():
        return {}

    result = _call_assessment_llm(doc.text)
    if result is None:
        return {}

    # Every evidence string must be a literal substring of doc.text -- if
    # either one is present but fails that check, the entire assessment is
    # discarded (not just the offending field), per the spec.
    if result.relevance_evidence and result.relevance_evidence not in doc.text:
        return {}
    if result.eligibility_evidence and result.eligibility_evidence not in doc.text:
        return {}

    updates = {}

    if result.relevance_evidence:
        updates["llm_robotics_relevant"] = result.robotics_relevant
        updates["llm_relevance_evidence"] = result.relevance_evidence

    if result.eligibility_evidence:
        eligibility_dict = result.eligibility.model_dump() if hasattr(result.eligibility, "model_dump") else result.eligibility.dict()
        any_field_filled = False
        for field in ELIGIBILITY_FIELDS:
            value = eligibility_dict.get(field)
            if value is None:
                continue
            # Only fill what the deterministic layer left unresolved --
            # never overwrite an "explicit" (or already "llm") value.
            if enrichment.get(f"{field}_confidence") != "none":
                continue
            updates[field] = value
            updates[f"{field}_confidence"] = "llm"
            any_field_filled = True
        if any_field_filled:
            updates["eligibility_llm_evidence"] = result.eligibility_evidence

    return updates
