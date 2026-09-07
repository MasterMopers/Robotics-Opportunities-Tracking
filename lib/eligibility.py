"""Phase 3: model "can I, solo and unincorporated, actually enter this" as
structured fields instead of a contest-vs-grant binary that never answered
the question.

Two responsibilities live here:

- `extract_eligibility_fields(text, rules)` -- deterministic, regex-driven
  extraction of the eight eligibility columns (see rules.yaml's
  `eligibility_patterns`), each independently either a real extracted fact
  (confidence "explicit") or left unresolved (confidence "none", NEVER a
  guessed default). This is what used to be four of the entries in
  `reject_rules` (equity/incorporation/faculty-sponsor/team-size) plus the
  K-12/high-school-only entries -- those were always eligibility facts
  about a specific operator, not universal disqualifiers, and collapsing
  them into a flat REJECT threw the actual fact away (e.g. "team of 4"
  became "rejected" instead of a queryable max_team_size=4 that a
  4-person-team operator could actually pass).
- `evaluate(row, profile)` -- pure comparison of those stored facts against
  profile.yaml. Any hard requirement the profile fails returns
  ("ineligible", failing_field). Any requirement still unresolved
  (confidence "none") returns ("unknown", first_unresolved_field). Only an
  all-clear returns ("eligible", None).
"""

import re

from lib.negation import negated as _negated

# The eight columns from the spec, each paired with a `{field}_confidence`
# column taking explicit/llm/none.
FIELDS = (
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


def _find_first_match(text, patterns):
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m and not _negated(text, m.start()):
            return m
    return None


def _bool_field(text, rules, key):
    patterns = rules.get("eligibility_patterns", {}).get(key, [])
    m = _find_first_match(text, patterns)
    if m:
        return True, "explicit"
    return None, "none"


def extract_eligibility_fields(text: str, rules: dict) -> dict:
    """Returns a dict with the eight eligibility fields and their paired
    `{field}_confidence` values. Every field defaults to (None, "none") --
    never a guessed default -- unless a pattern in rules.yaml's
    eligibility_patterns explicitly matched (and wasn't negated, e.g. "no
    equity required")."""
    patterns = rules.get("eligibility_patterns", {})

    result = {}

    requires_incorporation, conf = _bool_field(text, rules, "requires_incorporation")
    result["requires_incorporation"] = requires_incorporation
    result["requires_incorporation_confidence"] = conf

    requires_faculty_sponsor, conf = _bool_field(text, rules, "requires_faculty_sponsor")
    result["requires_faculty_sponsor"] = requires_faculty_sponsor
    result["requires_faculty_sponsor_confidence"] = conf

    equity_required, conf = _bool_field(text, rules, "equity_required")
    result["equity_required"] = equity_required
    result["equity_required_confidence"] = conf

    requires_enrollment, conf = _bool_field(text, rules, "requires_enrollment")
    result["requires_enrollment"] = requires_enrollment
    result["requires_enrollment_confidence"] = conf

    # requires_us_person is the one boolean field that can be resolved to
    # an explicit False, not just True/unresolved -- but only when the page
    # itself states an open/worldwide eligibility, never inferred from the
    # absence of a citizenship-restriction phrase.
    us_person_true = _find_first_match(text, patterns.get("requires_us_person", []))
    us_person_false = _find_first_match(text, patterns.get("requires_us_person_false", []))
    if us_person_true:
        result["requires_us_person"] = True
        result["requires_us_person_confidence"] = "explicit"
    elif us_person_false:
        result["requires_us_person"] = False
        result["requires_us_person_confidence"] = "explicit"
    else:
        result["requires_us_person"] = None
        result["requires_us_person_confidence"] = "none"

    # Age bounds: an explicit "ages X-Y" range sets both at once; otherwise
    # each is tried independently. K-12/high-school-only phrasing is a
    # fixed-value max_age extractor (see rules.yaml eligibility_patterns.max_age).
    min_age = max_age = None
    min_conf = max_conf = "none"

    range_m = _find_first_match(text, patterns.get("age_range_numeric", []))
    if range_m:
        try:
            min_age, max_age = int(range_m.group(1)), int(range_m.group(2))
            min_conf = max_conf = "explicit"
        except (ValueError, IndexError):
            pass

    if min_age is None:
        m = _find_first_match(text, patterns.get("min_age_numeric", []))
        if m:
            try:
                min_age = int(m.group(1))
                min_conf = "explicit"
            except (ValueError, IndexError):
                pass

    if max_age is None:
        for entry in patterns.get("max_age", []):
            m = re.search(entry["pattern"], text, re.IGNORECASE)
            if m and not _negated(text, m.start()):
                max_age = entry["value"]
                max_conf = "explicit"
                break

    result["min_age"] = min_age
    result["min_age_confidence"] = min_conf
    result["max_age"] = max_age
    result["max_age_confidence"] = max_conf

    # Entry fee: 0 means confirmed free, None means unknown -- never guess
    # "probably free" from silence.
    entry_fee_usd = None
    fee_conf = "none"
    free_m = _find_first_match(text, patterns.get("entry_fee_free", []))
    if free_m:
        entry_fee_usd = 0.0
        fee_conf = "explicit"
    else:
        fee_m = _find_first_match(text, patterns.get("entry_fee_numeric", []))
        if fee_m:
            try:
                entry_fee_usd = float(fee_m.group(1).replace(",", ""))
                fee_conf = "explicit"
            except (ValueError, IndexError):
                pass
    result["entry_fee_usd"] = entry_fee_usd
    result["entry_fee_usd_confidence"] = fee_conf

    # Max team size: an explicit number, or "individual/solo entries only"
    # as an explicit 1.
    max_team_size = None
    team_conf = "none"
    solo_m = _find_first_match(text, patterns.get("max_team_size_solo", []))
    if solo_m:
        max_team_size = 1
        team_conf = "explicit"
    else:
        team_m = _find_first_match(text, patterns.get("max_team_size_numeric", []))
        if team_m:
            try:
                max_team_size = int(team_m.group(1))
                team_conf = "explicit"
            except (ValueError, IndexError):
                pass
    result["max_team_size"] = max_team_size
    result["max_team_size_confidence"] = team_conf

    return result


# --------------------------------------------------------------------------
# evaluate(): pure comparison of stored facts against profile.yaml
# --------------------------------------------------------------------------

def _fails(field, value, profile):
    if field == "requires_incorporation":
        return bool(value) and not profile.get("incorporated", False)
    if field == "requires_faculty_sponsor":
        return bool(value) and not profile.get("has_faculty_sponsor", False)
    if field == "requires_us_person":
        return bool(value) and not profile.get("us_person", False)
    if field == "min_age":
        return value is not None and profile.get("age") is not None and profile["age"] < value
    if field == "max_age":
        return value is not None and profile.get("age") is not None and profile["age"] > value
    if field == "entry_fee_usd":
        return value is not None and value > 0 and not profile.get("will_pay_entry_fee", False)
    if field == "max_team_size":
        return value is not None and profile.get("team_size", 1) > value
    if field == "requires_enrollment":
        return bool(value) and not profile.get("currently_enrolled", False)
    if field == "equity_required":
        return bool(value) and not profile.get("will_give_equity", False)
    return False


def _get(row, key):
    """Works against both sqlite3.Row and a plain dict."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def evaluate(row, profile: dict):
    """Returns (verdict, reason) with verdict in {"eligible", "ineligible",
    "unknown"}. `row` is anything dict-like (sqlite3.Row or a plain dict)
    exposing the eight eligibility columns and their `_confidence` pairs.

    A hard requirement the profile fails wins over an unresolved field
    elsewhere -- a definite disqualification shouldn't be masked by an
    unrelated unknown. Only if nothing definitively fails do unresolved
    fields turn the verdict into "unknown" instead of "eligible"."""
    resolved = {}
    for field in FIELDS:
        conf = _get(row, f"{field}_confidence")
        resolved[field] = conf in ("explicit", "llm")

    for field in FIELDS:
        if not resolved[field]:
            continue
        value = _get(row, field)
        if _fails(field, value, profile):
            return "ineligible", field

    for field in FIELDS:
        if not resolved[field]:
            return "unknown", field

    return "eligible", None
