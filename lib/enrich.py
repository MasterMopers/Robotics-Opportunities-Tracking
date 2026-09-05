"""Per-item enrichment: fetch an item's own page once and extract
deadline, money, team size, and which rule phrases are present.

This only ever runs on items that are new in this diff -- never the whole
page list -- per the cost constraint in the spec.
"""

import html
import re
from datetime import date

from dateutil import parser as dateutil_parser

DATE_FRAGMENT = (
    r"(?:[A-Z][a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"   # March 3, 2027
    r"|\d{1,2}/\d{1,2}/\d{2,4})"                                 # 3/3/27
)


def _compile_deadline_patterns(rules: dict):
    compiled = {"explicit": [], "relative": []}
    for tmpl in rules["deadline_patterns"]["explicit"]:
        pat = tmpl.replace("%DATE%", f"({DATE_FRAGMENT})")
        compiled["explicit"].append(re.compile(pat, re.IGNORECASE))
    for tmpl in rules["deadline_patterns"]["relative"]:
        compiled["relative"].append(re.compile(tmpl, re.IGNORECASE))
    return compiled


def extract_deadline(text: str, rules: dict, today: date = None):
    """Returns (deadline_date, deadline_confidence) with confidence in
    {explicit, relative, llm, none} -- "llm" is never set by this function,
    see lib/llm_enrich.py. The JSON-LD path only ever reads an explicit
    application/registration-deadline field, never an event's own
    startDate/endDate -- those are when the event happens, not when
    applications close, and mislabeling one as the other would be a real
    mistake, not just a missed extraction."""
    today = today or date.today()
    patterns = _compile_deadline_patterns(rules)

    for pat in patterns["explicit"]:
        m = pat.search(text)
        if m:
            raw = m.group(1)
            try:
                parsed = dateutil_parser.parse(raw, fuzzy=True, default=None)
            except (ValueError, OverflowError):
                continue
            return parsed.date().isoformat(), "explicit"

    for jsonld_pat in rules.get("deadline_jsonld_patterns", []):
        m = re.search(jsonld_pat, text, re.DOTALL)
        if m:
            raw = m.group(1)
            try:
                parsed = dateutil_parser.parse(raw, fuzzy=True, default=None)
            except (ValueError, OverflowError):
                continue
            return parsed.date().isoformat(), "explicit"

    for pat in patterns["relative"]:
        m = pat.search(text)
        if m:
            try:
                days = int(m.group(1))
            except (ValueError, IndexError):
                continue
            from datetime import timedelta

            return (today + timedelta(days=days)).isoformat(), "relative"

    return None, "none"


def extract_money(text: str, rules: dict):
    pat = re.compile(rules["money_pattern"], re.IGNORECASE)
    m = pat.search(text)
    return m.group(0).strip() if m else None


def extract_team_size(text: str, rules: dict):
    for tmpl in rules["team_size_patterns"]:
        m = re.search(tmpl, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def extract_signals(text: str, rules: dict):
    """Which contest/grant signal phrases are present in the text, and the
    reject rule (if any) that fires. Used both for scoring trust:low items
    and for making every classification decision debuggable from the db."""
    lower = text.lower()

    matched = {"contest": [], "grant": []}
    scores = {"contest": 0.0, "grant": 0.0}
    for cls in ("contest", "grant"):
        for sig in rules["signals"][cls]:
            if sig["phrase"].lower() in lower:
                matched[cls].append(sig["phrase"])
                scores[cls] += sig["weight"]

    reject = None
    for cls in ("contest", "grant"):
        for rule in rules["reject_rules"][cls]:
            for m in re.finditer(rule["pattern"], text, re.IGNORECASE):
                if _negated(text, m.start()):
                    continue
                reject = {"class": cls, "phrase": m.group(0), "label": rule["label"]}
                break
            if reject:
                break
        if reject:
            break

    return matched, scores, reject


_NEGATION_WORDS = re.compile(
    r"\b(no|not|non|without|zero|free of|isn't|doesn't|won't|never)\b[\s-]*$", re.IGNORECASE
)


def _negated(text: str, match_start: int, window: int = 20) -> bool:
    """True if a negation word sits immediately before the match, e.g. a
    page advertising "no equity funding" shouldn't trip the "equity" reject
    rule -- that's the opposite of what the rule is meant to catch."""
    preceding = text[max(0, match_start - window):match_start]
    return bool(_NEGATION_WORDS.search(preceding))


_PLACE_JUNK_TOKENS = (
    '"', "=", "<", ">", "\\", "{", "}", "[", "]", "$", "px;", "aria-", "svg",
    "children", "fetchpriority", "class=", "panel-label", "-title",
)
# Cut a capture at the first sign it has run on into a time/date/filler
# clause rather than staying a place name, e.g. "...held at 530pm on Aug
# 21st" or "Johns Hopkins University in the fall".
_PLACE_TRIM_TRIGGER = re.compile(
    r"\b(?:in the|during|this fall|this spring|this summer|this winter|where|for|"
    r"mon|tue|tues|wed|thu|thurs|fri|sat|sun|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\d{1,4}(?::\d{2})?\s*(?:am|pm)\b",
    re.IGNORECASE,
)


def _clean_place(candidate: str):
    """Reject regex captures that are HTML/JS noise rather than a real place
    name (e.g. a bare 'venue' match landing inside an aria-labelledby
    attribute), and trim an obviously real capture's trailing filler clause."""
    if not candidate:
        return None
    candidate = html.unescape(candidate).strip()
    m = _PLACE_TRIM_TRIGGER.search(candidate)
    if m:
        candidate = candidate[: m.start()].strip()
    candidate = candidate.rstrip(",.;:- ")
    if not (2 <= len(candidate) <= 80):
        return None
    if not re.search(r"[A-Za-z]", candidate):
        return None
    low = candidate.lower()
    if any(tok in low for tok in _PLACE_JUNK_TOKENS):
        return None
    return candidate


def extract_location(text: str, rules: dict):
    """City/venue + format (In-person / Remote / Hybrid), only ever from text
    the page actually states -- default is Unknown, never inferred from
    class/source. Returns (location, format, confidence) with confidence in
    {explicit, inferred, llm, none} -- explicit means a specific city/venue
    was captured, inferred means only a format phrase (no venue) was found.
    "llm" is never set by this function -- it's added only by the separate,
    opt-in lib/llm_enrich.py fallback when this function leaves the field as
    "none"."""
    location = None
    for pat in rules.get("location_city_patterns", []):
        for m in re.finditer(pat, text, re.DOTALL if "@type" in pat else 0):
            groups = [g for g in m.groups() if g]
            candidate = ", ".join(dict.fromkeys(g.strip() for g in groups))
            cleaned = _clean_place(candidate)
            if cleaned:
                location = cleaned
                break
        if location:
            break

    phrases = rules.get("location_format_phrases", {})
    has_in_person = any(re.search(p, text, re.IGNORECASE) for p in phrases.get("in_person", []))
    has_remote = any(re.search(p, text, re.IGNORECASE) for p in phrases.get("remote", []))
    has_hybrid = any(re.search(p, text, re.IGNORECASE) for p in phrases.get("hybrid", []))

    if has_hybrid or (has_in_person and has_remote):
        fmt = "Hybrid"
    elif location or has_in_person:
        fmt = "In-person"
    elif has_remote:
        fmt = "Remote"
    else:
        fmt = "Unknown"

    if location:
        confidence = "explicit"
    elif fmt != "Unknown":
        confidence = "inferred"
    else:
        confidence = "none"

    return location, fmt, confidence


def extract_participants(text: str, rules: dict):
    """A real participant/attendee count the page states, never an invented
    estimate. Returns (count, confidence) with confidence in
    {explicit, llm, none} -- "llm" is never set by this function, see
    lib/llm_enrich.py."""
    for pat in rules.get("participant_count_patterns", []):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", "")), "explicit"
            except ValueError:
                continue
    return None, "none"


def enrich_item(text: str, rules: dict):
    deadline_date, deadline_confidence = extract_deadline(text, rules)
    money_raw = extract_money(text, rules)
    team_size = extract_team_size(text, rules)
    matched, scores, reject = extract_signals(text, rules)
    location, location_format, location_confidence = extract_location(text, rules)
    participants_count, participants_confidence = extract_participants(text, rules)
    return {
        "deadline_date": deadline_date,
        "deadline_confidence": deadline_confidence,
        "money_raw": money_raw,
        "team_size": team_size,
        "matched_signals": matched,
        "scores": scores,
        "reject": reject,
        "location": location,
        "location_format": location_format,
        "location_confidence": location_confidence,
        "participants_count": participants_count,
        "participants_confidence": participants_confidence,
    }
