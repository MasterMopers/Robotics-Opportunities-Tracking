#!/usr/bin/env python3
"""Phase 2: derive `thresholds.relevance_floor` from data instead of
asserting it.

1. Load data/calibration_labels.json (Phase 0c).
2. Score every labeled item with lib/relevance.py's bucket scorer.
3. Sweep relevance_floor over every integer from the minimum to the
   maximum observed r.
4. At each floor, compute precision and recall against the labels (an
   item is "predicted relevant" when (c>=1 or |B|>=2) and r>=floor -- the
   same eligibility rule lib/relevance.py uses at runtime).
5. Select the floor maximizing precision subject to recall >= 0.9. If no
   floor reaches recall 0.9, select the floor maximizing F1 and log that
   the constraint was infeasible.
6. Write the selected value into rules.yaml under thresholds.relevance_floor
   programmatically (never hand-edited), and append the full sweep table
   to docs/PROBES.md.

Usage:
    python scripts/calibrate_floor.py
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from lib.relevance import score_relevance

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELS_PATH = os.path.join(ROOT, "data", "calibration_labels.json")
RULES_PATH = os.path.join(ROOT, "rules.yaml")
PROBES_PATH = os.path.join(ROOT, "docs", "PROBES.md")

RECALL_CONSTRAINT = 0.9


def _predict(r, c, buckets, floor):
    return (c >= 1 or len(buckets) >= 2) and r >= floor


def sweep(labels, rules):
    scored = []
    for item in labels:
        result = score_relevance(item["text"], rules)
        scored.append((item["relevant"], result["relevance_score"], result["relevance_core_hits"], result["relevance_buckets"]))

    r_values = [s[1] for s in scored]
    lo, hi = int(min(r_values)), int(max(r_values))

    table = []
    for floor in range(lo, hi + 1):
        tp = fp = tn = fn = 0
        for relevant, r, c, buckets in scored:
            pred = _predict(r, c, buckets, floor)
            if pred and relevant:
                tp += 1
            elif pred and not relevant:
                fp += 1
            elif not pred and relevant:
                fn += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        table.append(
            {"floor": floor, "tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall, "f1": f1}
        )
    return table


def select_floor(table):
    feasible = [row for row in table if row["recall"] >= RECALL_CONSTRAINT]
    if feasible:
        best = max(feasible, key=lambda row: (row["precision"], row["floor"]))
        return best, True
    best = max(table, key=lambda row: (row["f1"], row["floor"]))
    return best, False


def write_rules_floor(floor_value):
    with open(RULES_PATH) as f:
        text = f.read()
    new_text, n = re.subn(
        r"(relevance_floor:\s*)-?\d+(\.\d+)?",
        lambda m: f"{m.group(1)}{floor_value}",
        text,
        count=1,
    )
    if n != 1:
        raise RuntimeError("could not find thresholds.relevance_floor in rules.yaml to update")
    with open(RULES_PATH, "w") as f:
        f.write(new_text)


def append_probes(table, selected, feasible, total_labels):
    lines = []
    lines.append("\n## Phase 2: relevance_floor sweep (scripts/calibrate_floor.py)\n")
    lines.append(f"Swept against {total_labels} labeled items from data/calibration_labels.json "
                 f"(recall constraint: >= {RECALL_CONSTRAINT:.0%}).\n")
    lines.append("| floor | TP | FP | TN | FN | precision | recall | F1 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in table:
        marker = " **<- selected**" if row["floor"] == selected["floor"] else ""
        lines.append(
            f"| {row['floor']} | {row['tp']} | {row['fp']} | {row['tn']} | {row['fn']} | "
            f"{row['precision']:.2f} | {row['recall']:.2f} | {row['f1']:.2f} |{marker}"
        )
    lines.append("")
    if feasible:
        lines.append(
            f"Selected floor **{selected['floor']}**: maximizes precision "
            f"({selected['precision']:.2f}) subject to recall >= {RECALL_CONSTRAINT:.0%} "
            f"(achieved recall {selected['recall']:.2f})."
        )
    else:
        lines.append(
            f"**No floor reached the recall >= {RECALL_CONSTRAINT:.0%} constraint** against this label "
            f"set -- infeasible. Fell back to maximizing F1: selected floor **{selected['floor']}** "
            f"(precision {selected['precision']:.2f}, recall {selected['recall']:.2f}, F1 {selected['f1']:.2f})."
        )
    lines.append("")
    with open(PROBES_PATH, "a") as f:
        f.write("\n".join(lines) + "\n")


def main():
    if not os.path.exists(LABELS_PATH):
        print(f"error: {LABELS_PATH} not found -- run scripts/label_corpus.py first (Phase 0c).", file=sys.stderr)
        sys.exit(1)

    with open(LABELS_PATH) as f:
        labels = json.load(f)
    if not labels:
        print("error: calibration label set is empty.", file=sys.stderr)
        sys.exit(1)

    with open(RULES_PATH) as f:
        rules = yaml.safe_load(f)

    table = sweep(labels, rules)
    selected, feasible = select_floor(table)

    print(f"Swept floors {table[0]['floor']}..{table[-1]['floor']} over {len(labels)} labeled items.")
    for row in table:
        print(f"  floor={row['floor']:>3}  P={row['precision']:.2f}  R={row['recall']:.2f}  F1={row['f1']:.2f}  "
              f"(tp={row['tp']} fp={row['fp']} fn={row['fn']} tn={row['tn']})")
    print(f"{'Feasible' if feasible else 'INFEASIBLE (recall constraint unmet)'} -- selected floor = {selected['floor']}")

    write_rules_floor(selected["floor"])
    append_probes(table, selected, feasible, len(labels))
    print(f"Wrote thresholds.relevance_floor = {selected['floor']} to {RULES_PATH}")
    print(f"Appended sweep table to {PROBES_PATH}")


if __name__ == "__main__":
    main()
