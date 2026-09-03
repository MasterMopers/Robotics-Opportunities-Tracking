"""Deterministic, confidence-respecting country classification for the
resolved `location` string (not raw page text). Used only to exclude items
confidently outside the US from the rendered README -- an item whose
location is still unresolved ("Unknown"/None) is never excluded, only ones
where the text clearly names a non-US place. Keyword lists live in
rules.yaml's `geo` block, matching this project's "tunable data, not code"
convention for every other keyword list.
"""

import re


def _matches_any(text: str, phrases) -> bool:
    for phrase in phrases:
        if re.search(r"\b" + re.escape(str(phrase).lower()) + r"\b", text):
            return True
    return False


def classify_country(location, rules) -> str:
    """Returns "US", "non-US", or "unknown". Never guesses: an empty/None
    location, or one that matches neither list, is "unknown" -- not "US" and
    not excluded."""
    if not location or not str(location).strip():
        return "unknown"

    geo = rules.get("geo", {})
    text = str(location).lower()

    # Non-US country/region names are the most specific signal -- check
    # first so a coincidental abbreviation collision can't override a clear
    # foreign place name.
    if _matches_any(text, geo.get("non_us_markers", [])):
        return "non-US"

    if _matches_any(text, geo.get("us_markers", [])):
        return "US"

    # State abbreviations are the weakest signal (two letters can collide
    # with ordinary words), so they're only matched with a preceding
    # comma+space, e.g. ", TX" -- matches how these strings actually look
    # ("Austin, TX"), not bare "IN"/"OR"/"IA" appearing mid-sentence.
    for abbrev in geo.get("us_state_abbrevs", []):
        if re.search(r",\s*" + re.escape(str(abbrev).lower()) + r"\b", text):
            return "US"

    return "unknown"
