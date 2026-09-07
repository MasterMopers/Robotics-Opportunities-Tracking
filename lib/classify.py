"""Deterministic classification. Three outcomes: accepted, review, rejected.

Phase 2 adds a relevance gate that runs BEFORE the contest-vs-grant
scoring, for every trust level -- including trust: high. A trusted source
knowing its own category (contest vs. grant) does not make its items
robotics; Hackster/Devpost/MLH all carry plenty of non-robotics items, and
nothing before this gate ever asked "is this even about robotics" at all.
An item that fails the relevance gate goes to `review`, not `rejected` --
relevance is a "we can't confirm this is in scope" signal, not a hard
disqualifier the way an eligibility reject-rule is.

trust: high  -> (after the relevance gate) item takes the source's
                declared class directly, no scoring.
trust: low   -> (after the relevance gate) item is scored against both
                keyword sets; a clear winner by the configured margin
                wins, otherwise REVIEW.
Either path: a fired reject rule always wins and produces REJECT, checked
before the relevance gate -- an explicitly disqualifying item (e.g. K-12
only) doesn't need a relevance verdict to be rejected.
"""

def classify_item(source_class: str, trust: str, enrichment: dict, rules: dict):
    reject = enrichment.get("reject")
    if reject:
        return {
            "status": "rejected",
            "final_class": None,
            "reject_phrase": f"{reject['label']}: \"{reject['phrase']}\"",
        }

    if not enrichment.get("relevance_eligible", True):
        return {"status": "review", "final_class": None, "reject_phrase": None}

    if trust == "high":
        # source_class is 'contest' or 'grant' for trust:high sources
        # (never 'both' -- see sources.yaml).
        return {"status": "accepted", "final_class": source_class, "reject_phrase": None}

    scores = enrichment["scores"]
    contest_score = scores["contest"]
    grant_score = scores["grant"]
    floor = rules["thresholds"]["floor"]
    margin = rules["thresholds"]["margin"]

    if contest_score < floor and grant_score < floor:
        return {"status": "review", "final_class": None, "reject_phrase": None}

    diff = abs(contest_score - grant_score)
    if diff < margin:
        return {"status": "review", "final_class": None, "reject_phrase": None}

    winner = "contest" if contest_score > grant_score else "grant"
    return {"status": "accepted", "final_class": winner, "reject_phrase": None}
