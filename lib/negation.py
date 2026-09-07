"""Shared negation-window check, used by both lib/enrich.py (reject-rule
matching) and lib/eligibility.py (eligibility-pattern matching) so a phrase
like "no equity required" doesn't trip an "equity" match in either place.
Split into its own module so neither of those two modules has to import
the other."""

import re

NEGATION_WORDS = re.compile(
    r"\b(no|not|non|without|zero|free of|isn't|doesn't|won't|never)\b[\s-]*$", re.IGNORECASE
)


def negated(text: str, match_start: int, window: int = 20) -> bool:
    """True if a negation word sits immediately before the match, e.g. a
    page advertising "no equity funding" shouldn't trip the "equity" reject
    rule -- that's the opposite of what the rule is meant to catch."""
    preceding = text[max(0, match_start - window):match_start]
    return bool(NEGATION_WORDS.search(preceding))
