#!/usr/bin/env python3
"""Rewrites only the block between the autogen markers in README.md, from
state.db. Everything outside that block is hand-written and survives."""

import os
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import yaml

from lib import db
from lib import eligibility as eligibility_lib
from lib.geo import classify_country
from lib.rank import compute_fit_scores, parse_money_value
from monitor import PROFILE_PATH, RULES_PATH, SOURCES_PATH, _next_occurrence

# --- tunable constants -----------------------------------------------------
CLOSING_SOON_DAYS = 14          # discovered contests inside this many days of
                                 # their deadline show up in "Closing soon"
ACT_NOW_CAP = 15                # Phase 7: "5 to 15 opportunities the operator
                                 # could actually enter," not a wide skim list
STALE_GRANT_MONTHS = 12         # a rolling grant with no activity this long
                                 # is marked stale (per spec section 3 table)
# ----------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "state.db")
README_PATH = os.path.join(ROOT, "README.md")

BEGIN_MARKER = "<!-- BEGIN AUTOGEN -->"
END_MARKER = "<!-- END AUTOGEN -->"

# Kept for any external caller still importing the old name; identical to
# lib.rank.parse_money_value (Phase 7 moved the implementation there so
# lib/rank.py doesn't have to import render_readme.py).
money_sort_key = parse_money_value


def esc(cell):
    """Escape a table cell: pipes break markdown tables, newlines break rows."""
    return str(cell).replace("|", "\\|").replace("\n", " ").strip() if cell else cell


def location_cell(item):
    fmt = item["location_format"] or "Unknown"
    place = item["location"]
    if fmt == "Unknown":
        return "Unknown"
    if place:
        return f"{fmt} ({esc(place)})"
    return fmt


def participants_cell(item):
    n = item["participants_count"]
    return f"{n:,}" if n is not None else "Unknown"


def table(headers, rows):
    if not rows:
        return None
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend(rows)
    return "\n".join(lines)


ELIGIBILITY_LABEL = {"eligible": "Eligible", "unknown": "Unknown", None: "Unknown"}


def render_act_now_row(item, fit):
    deadline = item["deadline_date"] or "rolling/unknown"
    money = item["money_raw"] or "n/a"
    kind = "Contest" if item["final_class"] == "contest" else "Grant"
    elig = ELIGIBILITY_LABEL.get(item["eligibility"], "Unknown")
    return (
        f"| [{esc(item['title'])}]({item['url']}) | {kind} | {esc(money)} | "
        f"{location_cell(item)} | {deadline} | {elig} | {fit:.2f} |"
    )


def build_autogen_block(conn):
    today = date.today()
    now_est = datetime.now(ZoneInfo("America/New_York"))
    now = now_est.strftime("%Y-%m-%d %H:%M %Z")

    rules = yaml.safe_load(open(RULES_PATH))
    profile = yaml.safe_load(open(PROFILE_PATH))["profile"]

    accepted_all = conn.execute(
        "SELECT * FROM items WHERE status='accepted' AND final_class IN ('contest', 'grant')"
    ).fetchall()

    # US-only filter: excludes only items whose resolved location string
    # confidently names a non-US place. An item with no resolved location
    # (still "Unknown") is left visible -- excluding it would be a guess,
    # and this project never guesses. Rows stay 'accepted' in state.db
    # either way; this is a render-time filter only, fully auditable here.
    #
    # Event location and requires_us_person are deliberately independent
    # signals: a remote contest hosted abroad may still be open to US
    # entrants (requires_us_person can be false or unknown regardless of
    # where the organizer sits), and a US-hosted contest may explicitly be
    # closed to non-US entrants. This filter only ever looks at the
    # resolved event `location`, never at requires_us_person, and
    # lib/eligibility.py's requires_us_person check never looks at
    # `location` either -- neither one substitutes for the other.
    accepted = [r for r in accepted_all if classify_country(r["location"], rules) != "non-US"]
    non_us_excluded = len(accepted_all) - len(accepted)

    ineligible = [r for r in accepted if r["eligibility"] == "ineligible"]
    rankable = [r for r in accepted if r["eligibility"] != "ineligible"]

    ranking_weights = rules["ranking"]
    scored = compute_fit_scores(rankable, ranking_weights, today=today)

    act_now = scored[:ACT_NOW_CAP]
    missed_cap = scored[ACT_NOW_CAP:]
    # Only "unknown"-eligibility rows that missed the Act-now cap go to
    # Needs review -- a genuinely eligible row that just didn't rank in the
    # top ACT_NOW_CAP is intentionally not shown anywhere else. That's the
    # point of capping: "5 to 15 opportunities," not everything accepted
    # dumped into a second list right below it. It stays 'accepted' in
    # state.db regardless.
    unknown_missed_cap = [(r, fit) for r, fit in missed_cap if r["eligibility"] in (None, "unknown")]

    review_items = conn.execute(
        "SELECT * FROM items WHERE status='review' ORDER BY first_seen DESC"
    ).fetchall()
    broken_sources = conn.execute(
        "SELECT * FROM source_health WHERE last_status NOT IN ('OK', 'DISABLED') ORDER BY source_id"
    ).fetchall()

    # Ineligible rows are never listed individually in the README (the
    # operator can't enter them) -- but a collapsed count with WHY each one
    # failed is what makes a bad eligibility extractor visible instead of
    # silently swallowing rows. lib.eligibility.evaluate() is re-run here
    # (cheap, no network) rather than persisting a redundant reason column.
    ineligible_reason_counts = {}
    for row in ineligible:
        _verdict, reason = eligibility_lib.evaluate(row, profile)
        ineligible_reason_counts[reason] = ineligible_reason_counts.get(reason, 0) + 1

    closing_soon = []
    for c in accepted:
        if c["final_class"] != "contest" or not c["deadline_date"]:
            continue
        try:
            d = datetime.strptime(c["deadline_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        days_left = (d - today).days
        if 0 <= days_left <= CLOSING_SOON_DAYS:
            closing_soon.append((days_left, c))

    calendar_entries = yaml.safe_load(open(SOURCES_PATH)).get("calendar", [])

    lines = [BEGIN_MARKER, ""]
    lines.append(
        f"### \U0001F4E1 {len(act_now)} actionable opportunit{'y' if len(act_now) == 1 else 'ies'} "
        f"right now \u00b7 {len(accepted)} accepted total \u00b7 updated {now}"
    )
    lines.append("")
    if non_us_excluded:
        lines.append(
            f"_{non_us_excluded} item(s) excluded below as confidently non-US "
            f"(location text names a specific non-US place). Items whose location is still "
            f"Unknown are kept visible, not excluded -- this filter only removes what we can "
            f"actually tell is outside the US, never a guess._"
        )
        lines.append("")

    lines.append("## Act now")
    lines.append(
        f"_The {ACT_NOW_CAP} highest-fit opportunities that aren't confirmed ineligible, ranked by "
        f"relevance, eligibility, prize/grant size, and deadline urgency (see lib/rank.py). This is "
        f"the section worth reading end to end -- everything else below is either time-sensitive, "
        f"unresolved, or explicitly out of reach._"
    )
    lines.append("")
    if act_now:
        rows = [render_act_now_row(r, fit) for r, fit in act_now]
        lines.append(table(["Opportunity", "Type", "Prize/Amount", "Format (location)", "Deadline", "Eligibility", "Fit"], rows))
    else:
        lines.append("_Nothing actionable right now._")
    lines.append("")

    lines.append("## Closing soon")
    lines.append(f"_Contests inside {CLOSING_SOON_DAYS} days of their deadline (regardless of Act-now rank)._")
    lines.append("")
    if closing_soon:
        rows = [
            f"| [{esc(c['title'])}]({c['url']}) | {c['deadline_date']} | {days_left} |"
            for days_left, c in sorted(closing_soon, key=lambda t: t[0])
        ]
        lines.append(table(["Contest", "Deadline", "Days left"], rows))
    else:
        lines.append(f"_Nothing closing in the next {CLOSING_SOON_DAYS} days._")
    lines.append("")

    lines.append("## Watch calendar")
    lines.append("_Annual/monthly programs tracked by date instead of scraping a page that's static for 11 months._")
    lines.append("")
    if calendar_entries:
        rows = []
        for entry in sorted(
            calendar_entries,
            key=lambda e: _next_occurrence(e["month"], e["day"], today, monthly=(e["month"] == 0)),
        ):
            target = _next_occurrence(entry["month"], entry["day"], today, monthly=(entry["month"] == 0))
            days_until = (target - today).days
            flag = "" if entry.get("confirmed", True) else " *(date unconfirmed, inferred from prior years)*"
            rows.append(
                f"| [{esc(entry['name'])}]({entry['link']}) | {target.isoformat()}{flag} | "
                f"{days_until} | alert {entry['lead_days']}d out |"
            )
        lines.append(table(["Program", "Next occurrence", "Days away", "Alert window"], rows))
    else:
        lines.append("_No calendar entries configured._")
    lines.append("")

    lines.append("## Needs review")
    lines.append("")
    lines.append("### Eligibility unclear")
    lines.append(
        "_Relevant and not confirmed ineligible, but at least one eligibility fact is still "
        "unresolved and the item didn't rank into Act now -- a quick human look could move it up "
        "or rule it out._"
    )
    lines.append("")
    if unknown_missed_cap:
        rows = [
            f"| [{esc(r['title'])}]({r['url']}) | {r['final_class'] or 'unclear'} | {fit:.2f} |"
            for r, fit in unknown_missed_cap
        ]
        lines.append(table(["Item", "Class", "Fit"], rows))
    else:
        lines.append("_Nothing pending eligibility review outside Act now._")
    lines.append("")

    lines.append("### Classification unclear")
    lines.append(
        "_The classifier couldn't confidently call these contest vs. grant vs. neither -- "
        "worth a quick human look rather than being silently dropped._"
    )
    lines.append("")
    if review_items:
        rows = [
            f"| [{esc(r['title'])}]({r['url']}) | {r['source_id']} | {r['contest_score']} | {r['grant_score']} |"
            for r in review_items
        ]
        lines.append(table(["Item", "Source", "Contest score", "Grant score"], rows))
    else:
        lines.append("_Nothing pending classification review._")
    lines.append("")

    lines.append("## Ineligible")
    lines.append(
        f"_{len(ineligible)} accepted item(s) are confirmed ineligible for this operator's profile "
        f"(profile.yaml) and are not listed individually -- but the failing reasons are tallied below "
        f"so a bad eligibility extractor would be visible here, not silent._"
    )
    lines.append("")
    if ineligible_reason_counts:
        rows = [
            f"| {reason} | {count} |"
            for reason, count in sorted(ineligible_reason_counts.items(), key=lambda kv: -kv[1])
        ]
        lines.append(table(["Failing field", "Count"], rows))
    else:
        lines.append("_No accepted items are currently ineligible._")
    lines.append("")

    lines.append("## Sources needing attention")
    lines.append("")
    if broken_sources:
        rows = [
            f"| {s['source_id']} | {s['last_status']} | {s['last_count']} | {esc(s['last_error']) or ''} |"
            for s in broken_sources
        ]
        lines.append(table(["Source", "Status", "Last count", "Error"], rows))
    else:
        lines.append("_All sources healthy as of last run._")
    lines.append("")

    lines.append(END_MARKER)
    return "\n".join(lines)


def render():
    db.init_db(DB_PATH)
    with db.connect(DB_PATH) as conn:
        block = build_autogen_block(conn)

    if not os.path.exists(README_PATH):
        content = f"# Robotics-Opportunities-Tracking\n\n{block}\n"
    else:
        with open(README_PATH) as f:
            content = f.read()
        pattern = re.compile(
            re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL
        )
        if pattern.search(content):
            content = pattern.sub(block, content)
        else:
            sep = "\n\n" if not content.endswith("\n") else "\n"
            content = content + sep + block + "\n"

    with open(README_PATH, "w") as f:
        f.write(content)
    print(f"README.md autogen block rewritten ({len(block)} chars).")


if __name__ == "__main__":
    render()
