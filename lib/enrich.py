"""Per-item enrichment: fetch an item's own page once, run it through
lib/extract.py, and extract deadline, money, team size, signal phrases,
location, and participant count from the two views that produces --
clean prose text and structured (JSON-LD/__NEXT_DATA__/__NUXT__) payloads.

This only ever runs on items that are new in this diff -- never the whole
page list -- per the cost constraint in the spec.
"""

import html
import re
from datetime import date

from dateutil import parser as dateutil_parser

from lib.extract import Document

DATE_FRAGMENT = (
    r"(?:[A-Z][a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"   # March 3, 2027
    r"|\d{1,2}/\d{1,2}/\d{2,4})"                                 # 3/3/27
)

# JSON-LD deadline fields that legitimately mean "applications/entries close
# on this date". Deliberately does NOT include startDate/endDate -- those
# are the event's own dates, not an application deadline (see
# test_jsonld_start_date_is_not_treated_as_deadline).
_JSONLD_DEADLINE_FIELDS = ("applicationDeadline", "submissionDeadline", "deadlineDate", "registrationDeadline")


def _iter_dicts(obj):
    """Yield every dict found anywhere in a nested structured payload
    (walked by key/value, not by regex over a string)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _has_type(d: dict, type_name: str) -> bool:
    t = d.get("@type")
    if isinstance(t, list):
        return type_name in t
    return t == type_name


def _find_event_dicts(structured):
    """Every dict anywhere in `structured` whose @type is (or includes)
    "Event" -- never an Organization/Place/Person block, so an
    unrelated publisher/organizer address can't be mistaken for the
    event's own deadline or location."""
    for top in structured or []:
        for d in _iter_dicts(top):
            if _has_type(d, "Event"):
                yield d


def _compile_deadline_patterns(rules: dict):
    compiled = {"explicit": [], "relative": []}
    for tmpl in rules["deadline_patterns"]["explicit"]:
        pat = tmpl.replace("%DATE%", f"({DATE_FRAGMENT})")
        compiled["explicit"].append(re.compile(pat, re.IGNORECASE))
    for tmpl in rules["deadline_patterns"]["relative"]:
        compiled["relative"].append(re.compile(tmpl, re.IGNORECASE))
    return compiled


def _parse_date_or_none(raw):
    try:
        parsed = dateutil_parser.parse(raw, fuzzy=True, default=None)
    except (ValueError, OverflowError, TypeError):
        return None
    return parsed.date().isoformat()


def extract_deadline(doc: Document, rules: dict, today: date = None):
    """Returns (deadline_date, deadline_confidence) with confidence in
    {explicit, relative, llm, none} -- "llm" is never set by this function,
    see lib/llm_enrich.py.

    JSON-LD deadlines are read by walking doc.structured for a dict whose
    @type is "Event" and pulling an explicit application/registration
    deadline field from it -- never startDate/endDate, and never a key
    lookup by regex over raw text (that was the pre-Phase-1 approach and is
    why the JSON-LD payload, which lives inside a <script> tag stripped out
    of doc.text, was never actually reachable by the old text-regex path in
    the first place on JS-rendered pages)."""
    today = today or date.today()
    text = doc.text
    patterns = _compile_deadline_patterns(rules)

    for pat in patterns["explicit"]:
        m = pat.search(text)
        if m:
            parsed = _parse_date_or_none(m.group(1))
            if parsed:
                return parsed, "explicit"

    for event in _find_event_dicts(doc.structured):
        for field_name in _JSONLD_DEADLINE_FIELDS:
            raw = event.get(field_name)
            if raw:
                parsed = _parse_date_or_none(str(raw))
                if parsed:
                    return parsed, "explicit"

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


# Cut a capture at the first sign it has run on into a time/date/filler
# clause rather than staying a place name, e.g. "...held at 530pm on Aug
# 21st" or "Johns Hopkins University in the fall". The HTML/JS-noise token
# filter that used to live alongside this (_PLACE_JUNK_TOKENS: `aria-`,
# `svg`, `<`, `=`, ...) is gone as of Phase 1 -- it existed only to clean up
# noise from matching against raw HTML, which lib/extract.py's clean-text
# pass now prevents from ever reaching this function in the first place.
_PLACE_TRIM_TRIGGER = re.compile(
    r"\b(?:in the|during|this fall|this spring|this summer|this winter|where|for|"
    r"mon|tue|tues|wed|thu|thurs|fri|sat|sun|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\d{1,4}(?::\d{2})?\s*(?:am|pm)\b",
    re.IGNORECASE,
)


def _clean_place(candidate: str):
    """Trim an obviously real capture's trailing filler clause and enforce
    a sane length bound. Rejecting HTML/JS noise is no longer this
    function's job -- see the _PLACE_TRIM_TRIGGER docstring above."""
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
    return candidate


def extract_location(doc: Document, rules: dict):
    """City/venue + format (In-person / Remote / Hybrid), only ever from
    text/structured data the page actually states -- default is Unknown,
    never inferred from class/source. Returns (location, format, confidence)
    with confidence in {explicit, inferred, llm, none} -- explicit means a
    specific city/venue was captured, inferred means only a format phrase
    (no venue) was found. "llm" is never set by this function -- it's added
    only by the separate, opt-in lib/llm_enrich.py fallback when this
    function leaves the field as "none".

    JSON-LD location is read from Event.location.address specifically (by
    key lookup on doc.structured), never from an Organization block --
    see test_ignores_organization_address_without_event_type, the Hackster
    HQ-address regression this guards against."""
    text = doc.text
    location = None

    for event in _find_event_dicts(doc.structured):
        loc = event.get("location")
        address = loc.get("address") if isinstance(loc, dict) else None
        if isinstance(address, dict):
            city = address.get("addressLocality")
            region = address.get("addressRegion")
            if city:
                parts = [p for p in (city, region) if p]
                candidate = ", ".join(dict.fromkeys(p.strip() for p in parts))
                cleaned = _clean_place(candidate)
                if cleaned:
                    location = cleaned
                    break

    if not location:
        # Structural DOM markers (e.g. MLH's hero-location CSS class),
        # captured by lib/extract.py as plain {"@type": "LocationHint", ...}
        # dicts -- a key lookup, not a regex over raw markup.
        for item in doc.structured or []:
            if isinstance(item, dict) and item.get("@type") == "LocationHint":
                cleaned = _clean_place(item.get("text"))
                if cleaned:
                    location = cleaned
                    break

    if not location:
        for pat in rules.get("location_city_patterns", []):
            for m in re.finditer(pat, text):
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


def extract_participants(doc: Document, rules: dict):
    """A real participant/attendee count the page states, never an invented
    estimate. Returns (count, confidence) with confidence in
    {explicit, llm, none} -- "llm" is never set by this function, see
    lib/llm_enrich.py."""
    text = doc.text
    for pat in rules.get("participant_count_patterns", []):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", "")), "explicit"
            except ValueError:
                continue
    return None, "none"


def enrich_item(doc: Document, rules: dict):
    deadline_date, deadline_confidence = extract_deadline(doc, rules)
    money_raw = extract_money(doc.text, rules)
    team_size = extract_team_size(doc.text, rules)
    matched, scores, reject = extract_signals(doc.text, rules)
    location, location_format, location_confidence = extract_location(doc, rules)
    participants_count, participants_confidence = extract_participants(doc, rules)
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
