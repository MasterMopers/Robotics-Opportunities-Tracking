"""Phase 2: the relevance axis. Nothing in classify.py/enrich.py decided
*whether an item is about robotics at all* before this module existed --
contest-vs-grant scoring and accept/review/reject are a different question,
and a software hackathon with a 4-person cap and no equity clause used to
pass every existing gate just fine. This is a missing dimension, not a
threshold to tune on top of the old scoring.

"Robotics" is defined broadly on purpose: mechanical, electrical, and
software, in any combination. A single `core` term (unambiguously robotics
-- "robot", "SLAM", "gripper", ...) qualifies an item on its own. Two of the
three non-core disciplines (mechanical/electrical/software) together also
qualify, even with zero core hits -- that's what "any combination" means.
One non-core discipline alone does not qualify, which is the mechanism that
keeps a generic AI/web hackathon (which will often hit `software` alone via
"reinforcement learning" or "computer vision") out.

Term lists live in rules.yaml (`signals.relevance`), not here, matching the
project's "tuning lives in YAML, not code" invariant -- adding a keyword
after a misclassification is a one-line edit there. `thresholds.relevance_floor`
is *derived*, not hand-tuned: it's written by scripts/calibrate_floor.py
from a sweep against data/calibration_labels.json, never edited directly.
"""

import functools
import re

_BUCKET_NAMES = ("mechanical", "electrical", "software")


@functools.lru_cache(maxsize=None)
def _compile_phrase(phrase: str):
    """Word-boundary match, not substring. Regression fixed here: naive
    substring matching on "stem" produced false positives from "system" and
    "ecosystem" -- re.escape + \\b on both ends closes that off, the same
    fix the spec calls for in extract_signals()."""
    return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)


def _matched_phrases(text: str, phrases) -> list:
    return [p for p in phrases if _compile_phrase(p).search(text)]


def score_relevance(text: str, rules: dict) -> dict:
    """Returns a dict with the raw hit lists/counts, the computed score,
    and whether the item passes the relevance gate:

        r = 3*c + 2*|B| - 2*a

    where c = number of distinct `core` phrases matched, B = the set of
    non-core buckets (mechanical/electrical/software) with >=1 match, and
    a = number of distinct `anti` phrases matched. Eligible when
    (c >= 1 or |B| >= 2) and r >= thresholds.relevance_floor.
    """
    relevance_rules = rules.get("signals", {}).get("relevance", {})

    core_hits = _matched_phrases(text, relevance_rules.get("core", []))
    bucket_hits = {b: _matched_phrases(text, relevance_rules.get(b, [])) for b in _BUCKET_NAMES}
    anti_hits = _matched_phrases(text, relevance_rules.get("anti", []))

    c = len(core_hits)
    buckets = sorted(b for b in _BUCKET_NAMES if bucket_hits[b])
    a = len(anti_hits)
    r = 3 * c + 2 * len(buckets) - 2 * a

    floor = rules.get("thresholds", {}).get("relevance_floor", 0)
    eligible = (c >= 1 or len(buckets) >= 2) and r >= floor

    terms = list(core_hits)
    for b in buckets:
        terms.extend(bucket_hits[b])
    terms.extend(anti_hits)

    return {
        "relevance_score": float(r),
        "relevance_core_hits": c,
        "relevance_buckets": buckets,
        "relevance_terms": terms,
        "relevance_eligible": eligible,
    }
