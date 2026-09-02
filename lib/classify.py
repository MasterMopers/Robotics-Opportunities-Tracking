"""Deterministic classification. Three outcomes: accepted, review, rejected.

trust: high  -> item takes the source's declared class directly, no scoring.
trust: low   -> item is scored against both keyword sets; a clear winner by
                the configured margin wins, otherwise REVIEW.
Either path: a fired reject rule always wins and produces REJECT.
"""


def classify_item(source_class: str, trust: str, enrichment: dict, rules: dict):
    reject = enrichment.get("reject")
    if reject:
        return {
            "status": "rejected",
            "final_class": None,
            "reject_phrase": f"{reject['label']}: \"{reject['phrase']}\"",
        }

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
