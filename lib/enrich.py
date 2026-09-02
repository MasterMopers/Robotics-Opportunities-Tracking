"""Per-item enrichment: fetch an item's own page once and extract
deadline, money, team size, and which rule phrases are present.

This only ever runs on items that are new in this diff -- never the whole
page list -- per the cost constraint in the spec.
"""

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


def enrich_item(text: str, rules: dict):
    deadline_date, deadline_confidence = extract_deadline(text, rules)
    money_raw = extract_money(text, rules)
    team_size = extract_team_size(text, rules)
    matched, scores, reject = extract_signals(text, rules)
    return {
        "deadline_date": deadline_date,
        "deadline_confidence": deadline_confidence,
        "money_raw": money_raw,
        "team_size": team_size,
        "matched_signals": matched,
        "scores": scores,
        "reject": reject,
    }
