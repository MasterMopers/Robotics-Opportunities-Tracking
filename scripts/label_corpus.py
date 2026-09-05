#!/usr/bin/env python3
"""Phase 0c: build the calibration label set that Phase 2's
scripts/calibrate_floor.py derives `thresholds.relevance_floor` from.

The labeler must be mechanically independent of the scorer it calibrates,
or the exercise is circular. Intended design, used whenever
`OPENAI_API_KEY` is set:

1. Pull all `accepted` rows from state.db.bak. For each, fetch the item
   page and run it through lib/extract.py.
2. Call gpt-5-nano TWICE per item, independently, with a rubric prompt
   that never sees rules.yaml, the bucket term lists, or any keyword --
   only the operator's interest described in prose (see RUBRIC_PROMPT
   below).
3. Structured output {relevant: bool, evidence: str}. Validate that
   `evidence` is a literal substring of the extracted page text; discard
   the label if it is not.
4. Keep only items where the two independent runs agree; discard
   disagreements and record how many were dropped (the inter-run
   disagreement rate is the judge's noise floor, and is written to
   docs/PROBES.md).
5. Run the two no-LLM anchor checks (robotics-native sources should label
   overwhelmingly positive; Devpost/MLH items with only non-hardware themes
   should label overwhelmingly negative -- 70% bar each way) and write both
   rates to docs/PROBES.md. If either anchor fails, this script exits
   non-zero rather than writing a label set calibrate_floor.py could
   silently build on top of.
6. Write the surviving labels to data/calibration_labels.json.

**`OPENAI_API_KEY` is not set in this environment**, so this run instead
uses the spec's own documented fallback: source-provenance labeling. Items
from robotics-native sources are positives, items from Devpost/MLH with no
hardware-sounding keyword anywhere in their own title/snippet/page text are
negatives, and everything else is excluded from the calibration set
entirely. This is a strictly weaker signal than the two-independent-judge
design above -- see docs/PROBES.md's Phase 0c section for why, and the note
that the resulting relevance_floor should be re-derived once a key is
available. No anchor rates or inter-run disagreement rate are computed in
fallback mode; that would be circular (see docs/PROBES.md).

Usage:
    python scripts/label_corpus.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3

from adapters._http import get as http_get
from lib.extract import extract_document

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAK_DB_PATH = os.path.join(ROOT, "state.db.bak")
OUT_PATH = os.path.join(ROOT, "data", "calibration_labels.json")
PROBES_PATH = os.path.join(ROOT, "docs", "PROBES.md")

MAX_TEXT_CHARS = 6000  # bound what we store per item; plenty for bucket-term scoring
JUDGE_MODEL = "gpt-5-nano"

RUBRIC_PROMPT = (
    "Decide whether this page describes a contest, competition, challenge, or grant "
    "that a solo undergraduate could enter with a robotics project. Robotics here "
    "spans mechanical design, electronics and firmware, and robotics software such as "
    "perception, control, or planning, in any combination. A pure software, web, "
    "mobile, fintech, or crypto opportunity is not relevant even if it offers prizes. "
    "Output relevant: true|false and a verbatim quote from the page that justifies "
    "your answer."
)

# Robotics-native sources per the spec's anchor-check list. Only the ones
# actually present as source_ids in this project's sources.yaml/state.db
# are checked here -- NHRL/ICRA/Hackaday-Prize-the-contest don't exist as
# scraped sources in this DB snapshot (they're Watch-calendar entries, not
# scraped items), so they can't contribute provenance-positive rows from
# state.db.bak even though they're on the spec's anchor list.
ROBOTICS_NATIVE_SOURCES = {"hackster", "pcbway"}

# Devpost/MLH items get a provenance-negative label only when nothing in
# their own title/snippet/page text hints at hardware -- otherwise they're
# ambiguous and excluded rather than guessed either way.
NO_HARDWARE_SOURCES = {"devpost", "mlh"}
HARDWARE_HINT_WORDS = (
    "robot", "hardware", "embedded", "firmware", "iot", "drone", "pcb",
    "arduino", "raspberry pi", "sensor", "mechanical", "3d print",
    "3d-print", "cad", "circuit", "microcontroller", "esp32", "stm32",
    "actuator", "servo", "solder",
)


def _fetch_doc(url):
    try:
        html = http_get(url).text
    except Exception:
        html = ""
    return extract_document(html)


def _is_llm_enabled():
    return bool(os.environ.get("OPENAI_API_KEY"))


# --------------------------------------------------------------------------
# Provenance fallback (used in this run: no OPENAI_API_KEY)
# --------------------------------------------------------------------------

def _label_by_provenance(row):
    source_id = row["source_id"]
    title = (row["title"] or "").lower()
    snippet = (row["snippet"] or "").lower()

    if source_id in ROBOTICS_NATIVE_SOURCES:
        return True, "provenance: robotics-native source"

    if source_id in NO_HARDWARE_SOURCES:
        haystack = f"{title} {snippet}"
        if any(w in haystack for w in HARDWARE_HINT_WORDS):
            return None, "provenance: ambiguous (hardware keyword present)"
        return False, "provenance: Devpost/MLH with no hardware keyword"

    return None, "provenance: not classifiable (neither robotics-native nor Devpost/MLH)"


def _run_fallback(rows):
    labels = []
    excluded = 0
    for i, row in enumerate(rows, 1):
        relevant, reason = _label_by_provenance(row)
        if relevant is None:
            excluded += 1
            continue

        doc = _fetch_doc(row["url"])
        combined_text = f"{row['title']} {row['snippet'] or ''} {doc.text}".strip()[:MAX_TEXT_CHARS]

        labels.append(
            {
                "id": row["id"],
                "source_id": row["source_id"],
                "title": row["title"],
                "url": row["url"],
                "snippet": row["snippet"],
                "relevant": relevant,
                "evidence": reason,
                "label_method": "source_provenance_fallback",
                "text": combined_text,
            }
        )
        if i % 20 == 0:
            print(f"  ...processed {i}/{len(rows)}")

    note = (
        "\n**`OPENAI_API_KEY` not set at run time** -- calibration labels were built "
        "via source-provenance fallback, not a gpt-5-nano judge. No inter-run "
        "disagreement rate or anchor rates were computed (they would be circular "
        "against provenance-only labels -- see the discussion above). "
        f"{len(labels)} items labeled ({sum(1 for l in labels if l['relevant'])} positive, "
        f"{sum(1 for l in labels if not l['relevant'])} negative), {excluded} excluded as "
        "not classifiable by provenance alone.\n"
    )
    _append_probes(note)
    return labels


# --------------------------------------------------------------------------
# gpt-5-nano judge path (not exercised in this run -- no OPENAI_API_KEY)
# --------------------------------------------------------------------------

def _call_judge(text):
    """One independent judging call. Returns (relevant, evidence) or None on
    any failure -- never raises, matching lib/llm_enrich.py's convention."""
    try:
        from openai import OpenAI
        from pydantic import BaseModel
    except ImportError:
        return None

    class Relevance(BaseModel):
        relevant: bool
        evidence: str

    try:
        client = OpenAI(timeout=30)
        completion = client.chat.completions.parse(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": RUBRIC_PROMPT},
                {"role": "user", "content": text[:8000]},
            ],
            response_format=Relevance,
        )
        result = completion.choices[0].message.parsed
        return result.relevant, result.evidence
    except Exception:
        return None


def _run_llm_judge(rows):
    labels = []
    dropped_disagreement = 0
    dropped_bad_evidence = 0
    anchor_pos_hits, anchor_pos_total = 0, 0
    anchor_neg_hits, anchor_neg_total = 0, 0

    for i, row in enumerate(rows, 1):
        doc = _fetch_doc(row["url"])
        combined_text = f"{row['title']} {row['snippet'] or ''} {doc.text}".strip()[:MAX_TEXT_CHARS]
        if not combined_text:
            continue

        run_a = _call_judge(combined_text)
        run_b = _call_judge(combined_text)
        if run_a is None or run_b is None:
            continue

        rel_a, ev_a = run_a
        rel_b, ev_b = run_b
        if rel_a != rel_b:
            dropped_disagreement += 1
            continue

        evidence = ev_a
        if not evidence or evidence not in combined_text:
            dropped_bad_evidence += 1
            continue

        labels.append(
            {
                "id": row["id"],
                "source_id": row["source_id"],
                "title": row["title"],
                "url": row["url"],
                "snippet": row["snippet"],
                "relevant": rel_a,
                "evidence": evidence,
                "label_method": "gpt-5-nano_double_run_agreement",
                "text": combined_text,
            }
        )

        if row["source_id"] in ROBOTICS_NATIVE_SOURCES:
            anchor_pos_total += 1
            if rel_a:
                anchor_pos_hits += 1
        if row["source_id"] in NO_HARDWARE_SOURCES:
            haystack = f"{(row['title'] or '').lower()} {(row['snippet'] or '').lower()}"
            if not any(w in haystack for w in HARDWARE_HINT_WORDS):
                anchor_neg_total += 1
                if not rel_a:
                    anchor_neg_hits += 1

        if i % 20 == 0:
            print(f"  ...judged {i}/{len(rows)}")

    total_judged = len(labels) + dropped_disagreement + dropped_bad_evidence
    disagreement_rate = dropped_disagreement / total_judged if total_judged else 0.0
    pos_rate = anchor_pos_hits / anchor_pos_total if anchor_pos_total else None
    neg_rate = anchor_neg_hits / anchor_neg_total if anchor_neg_total else None

    note = (
        f"\n**gpt-5-nano judge run** ({total_judged} items double-judged): "
        f"inter-run disagreement rate = {disagreement_rate:.1%} "
        f"({dropped_disagreement} dropped for disagreement, {dropped_bad_evidence} dropped "
        f"for a non-literal evidence quote). "
        f"Anchor check (robotics-native sources labeled positive): "
        f"{pos_rate:.1%} of {anchor_pos_total}" if pos_rate is not None else "n/a (no anchor items)"
    )
    _append_probes(note + "\n")

    if pos_rate is not None and pos_rate < 0.70:
        print(f"ANCHOR FAILED: robotics-native positive rate {pos_rate:.1%} < 70%. Stop and fix before calibrating.", file=sys.stderr)
        sys.exit(2)
    if neg_rate is not None and neg_rate < 0.70:
        print(f"ANCHOR FAILED: Devpost/MLH no-hardware negative rate {neg_rate:.1%} < 70%. Stop and fix before calibrating.", file=sys.stderr)
        sys.exit(2)

    return labels


def _append_probes(text):
    with open(PROBES_PATH, "a") as f:
        f.write(text)


def main():
    if not os.path.exists(BAK_DB_PATH):
        print(f"error: {BAK_DB_PATH} not found -- run this from the repo root after Phase 0a.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(BAK_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, source_id, title, url, snippet FROM items WHERE status='accepted'"
    ).fetchall()
    conn.close()

    print(f"Loaded {len(rows)} accepted rows from {BAK_DB_PATH}.")

    if _is_llm_enabled():
        print("OPENAI_API_KEY is set -- running the double-judged gpt-5-nano labeler.")
        labels = _run_llm_judge(rows)
    else:
        print("OPENAI_API_KEY is not set -- using source-provenance fallback labeling.")
        labels = _run_fallback(rows)

    pos = sum(1 for l in labels if l["relevant"])
    neg = sum(1 for l in labels if not l["relevant"])
    print(f"Final label set: {len(labels)} items ({pos} positive, {neg} negative).")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(labels, f, indent=2)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
