"""Phase 7: turn "everything accepted" into "5-15 things worth reading."

The README used to list every accepted contest/grant, sorted only by
deadline or amount within its own class. That's a wide skim list, not
something a solo operator can act on. `compute_fit_scores` produces one
unified ranking across BOTH contests and grants (an item's class doesn't
change how actionable it is) so render_readme.py can build a single
"Act now" section instead.

    fit = 0.40*r_hat + 0.30*e + 0.15*m_hat + 0.15*u

- r_hat: relevance_score normalized to [0, 1] over the current set being
  ranked (min-max over that set, not a fixed global scale -- "relevant
  relative to what's actually on offer right now").
- e: 1.0 for eligibility == "eligible", 0.4 for "unknown". Rows with
  eligibility == "ineligible" are excluded outright before this function
  is ever called (see render_readme.py) -- they never get a fit score at
  all, on purpose.
- m_hat: log10(1+m) / log10(1+m_max), m = the largest dollar figure parsed
  out of money_raw, m_max = the largest such figure across the set being
  ranked. Missing money (m_hat=0) is NOT the same as an average -- an
  unknown prize is not evidence of a large one.
- u: exp(-d/30), d = days remaining until deadline_date. Rows with no
  deadline get u=0.5 rather than being penalized for having no urgency
  signal at all -- but only when final_class == "grant". Grants are
  legitimately, routinely rolling (Awesome Foundation, most microgrants),
  so a missing deadline there isn't a red flag. A contest with no deadline
  gets u=0.0 instead: real contests almost always state one, so a missing
  deadline there is either an extraction gap or -- verified against a
  real case, PCBWay's "sponsored project" pages, which are OTHER
  builders' already-submitted projects, not a call for entries with a due
  date -- not really a time-boxed opportunity at all. See _urgency().

The four weights live in rules.yaml's `ranking:` block, not here -- tuning
the formula's balance should be a YAML edit, matching every other scoring
knob in this project.
"""

import math
from datetime import date

# Not weights (those are in rules.yaml) -- these are the two fixed
# constants the fit formula itself defines (spec: "e = 1 for eligible and
# 0.4 for unknown", "rows with no deadline take u = 0.5"), the same way
# render_readme.py's CLOSING_SOON_DAYS is a render-tuning constant in code
# rather than a scored weight in rules.yaml.
ELIGIBILITY_SCORE = {"eligible": 1.0, "unknown": 0.4}
NO_DEADLINE_URGENCY = 0.5


def parse_money_value(money_raw):
    """The largest dollar figure mentioned in a free-text money string
    (handles ranges like "$1,000-100,000" and a trailing k/K multiplier),
    or None if no figure is present -- never 0, since 0 would collide with
    a real, confirmed-free-of-charge figure elsewhere in this codebase."""
    if not money_raw:
        return None
    import re

    nums = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+(?:\.\d+)?", money_raw)]
    if not nums:
        return None
    value = max(nums)
    if re.search(r"\bk\b", money_raw, re.IGNORECASE) or money_raw.strip().lower().endswith("k"):
        value *= 1000
    return value


def _urgency(deadline_date, final_class, today):
    if not deadline_date:
        # The spec's "no deadline" carve-out names "rolling grants"
        # specifically, not "rolling items" generically -- grants are
        # legitimately, routinely rolling (Awesome Foundation, most
        # microgrants), so a missing deadline there isn't a red flag and
        # gets the neutral, non-penalizing 0.5. A CONTEST with no
        # deadline is a different situation: real contests almost always
        # state one, so a missing deadline there is either an extraction
        # gap or an ongoing community-submission page rather than a
        # time-boxed call for entries (verified against a real case: PCBWay
        # "sponsored project" pages are OTHER builders' already-submitted
        # projects, not a call for entries with a due date at all) -- rank
        # those below anything with a real, confirmed near-term deadline
        # rather than granting them the same neutral urgency a rolling
        # grant legitimately earns.
        return NO_DEADLINE_URGENCY if final_class == "grant" else 0.0
    try:
        d_date = date.fromisoformat(deadline_date)
    except (ValueError, TypeError):
        return NO_DEADLINE_URGENCY if final_class == "grant" else 0.0
    days_remaining = max(0, (d_date - today).days)
    return math.exp(-days_remaining / 30)


def compute_fit_scores(rows, weights: dict, today=None):
    """rows: dict-like items (sqlite3.Row or plain dict) exposing
    relevance_score, eligibility, money_raw, deadline_date, final_class.
    Every row
    passed in is scored -- callers are responsible for excluding
    eligibility == "ineligible" rows first (this function doesn't special-
    case that; it just computes e = 0.0 for anything not in
    ELIGIBILITY_SCORE, which would rank an ineligible row last, not
    excluded, if one slipped through).

    Returns a list of (row, fit) tuples sorted by fit descending."""
    today = today or date.today()

    relevance_scores = [r["relevance_score"] for r in rows if r["relevance_score"] is not None]
    r_min = min(relevance_scores) if relevance_scores else 0.0
    r_max = max(relevance_scores) if relevance_scores else 0.0

    money_values = [v for v in (parse_money_value(r["money_raw"]) for r in rows) if v is not None]
    m_max = max(money_values) if money_values else 0.0

    scored = []
    for row in rows:
        rel = row["relevance_score"]
        if rel is None or r_max == r_min:
            # All tied (or nothing to compare against) -- treat as fully
            # relevant relative to this set rather than dividing by zero.
            r_hat = 1.0
        else:
            r_hat = (rel - r_min) / (r_max - r_min)

        e = ELIGIBILITY_SCORE.get(row["eligibility"], 0.0)

        m = parse_money_value(row["money_raw"])
        m_hat = 0.0 if (m is None or m_max <= 0) else math.log10(1 + m) / math.log10(1 + m_max)

        u = _urgency(row["deadline_date"], row["final_class"], today)

        fit = (
            weights["relevance_weight"] * r_hat
            + weights["eligibility_weight"] * e
            + weights["money_weight"] * m_hat
            + weights["deadline_weight"] * u
        )
        scored.append((row, fit))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored
