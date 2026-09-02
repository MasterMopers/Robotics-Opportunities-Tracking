#!/usr/bin/env python3
"""Rewrites only the block between the autogen markers in README.md, from
state.db. Everything outside that block is hand-written and survives."""

import os
import re
from datetime import date, datetime, timezone

import yaml

from lib import db
from monitor import SOURCES_PATH, _next_occurrence

# --- tunable constants -----------------------------------------------------
CLOSING_SOON_DAYS = 14          # discovered contests inside this many days of
                                 # their deadline show up in "Closing soon"
STALE_GRANT_MONTHS = 12         # a rolling grant with no activity this long
                                 # is marked stale (per spec section 3 table)
# ----------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "state.db")
README_PATH = os.path.join(ROOT, "README.md")

BEGIN_MARKER = "<!-- BEGIN AUTOGEN -->"
END_MARKER = "<!-- END AUTOGEN -->"


def money_sort_key(money_raw):
    if not money_raw:
        return -1
    nums = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+(?:\.\d+)?", money_raw)]
    if not nums:
        return -1
    value = max(nums)
    if re.search(r"\bk\b", money_raw, re.IGNORECASE) or money_raw.strip().lower().endswith("k"):
        value *= 1000
    return value


def render_contest_row(item):
    deadline = item["deadline_date"] or "rolling/unknown"
    team = item["team_size"] or "n/a"
    money = item["money_raw"] or "n/a"
    return f"- **[{item['title']}]({item['url']})** -- prize: {money}, team size: {team}, deadline: {deadline}"


def render_grant_row(item):
    money = item["money_raw"] or "amount not extracted"
    deadline = item["deadline_date"] or "rolling"
    return f"- **[{item['title']}]({item['url']})** -- amount: {money}, eligibility: rolling/see link, deadline: {deadline}"


def build_autogen_block(conn):
    today = date.today()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    contests = conn.execute(
        "SELECT * FROM items WHERE final_class='contest' AND status='accepted' ORDER BY "
        "(deadline_date IS NULL), deadline_date ASC"
    ).fetchall()
    grants = conn.execute(
        "SELECT * FROM items WHERE final_class='grant' AND status='accepted'"
    ).fetchall()
    grants = sorted(grants, key=lambda r: money_sort_key(r["money_raw"]), reverse=True)
    review_items = conn.execute(
        "SELECT * FROM items WHERE status='review' ORDER BY first_seen DESC"
    ).fetchall()
    broken_sources = conn.execute(
        "SELECT * FROM source_health WHERE last_status != 'OK' ORDER BY source_id"
    ).fetchall()

    closing_soon = []
    for c in contests:
        if not c["deadline_date"]:
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
    lines.append(f"**Last updated: {now}**")
    lines.append("")

    lines.append("## Open contests")
    if contests:
        for c in contests:
            lines.append(render_contest_row(c))
    else:
        lines.append("_None currently open._")
    lines.append("")

    lines.append("## Available grants")
    if grants:
        for g in grants:
            lines.append(render_grant_row(g))
    else:
        lines.append("_None currently listed._")
    lines.append("")

    lines.append("## Closing soon")
    if closing_soon:
        for days_left, c in sorted(closing_soon, key=lambda t: t[0]):
            lines.append(f"- **[{c['title']}]({c['url']})** -- {days_left} day(s) left (deadline {c['deadline_date']})")
    else:
        lines.append("_Nothing closing in the next {} days._".format(CLOSING_SOON_DAYS))
    lines.append("")

    lines.append("## Watch calendar")
    if calendar_entries:
        for entry in sorted(
            calendar_entries,
            key=lambda e: _next_occurrence(e["month"], e["day"], today, monthly=(e["month"] == 0)),
        ):
            target = _next_occurrence(entry["month"], entry["day"], today, monthly=(entry["month"] == 0))
            days_until = (target - today).days
            flag = "" if entry.get("confirmed", True) else " (date unconfirmed, inferred from prior years)"
            lines.append(
                f"- [{entry['name']}]({entry['link']}) -- next: {target.isoformat()} "
                f"({days_until} days away), alert {entry['lead_days']}d out{flag}"
            )
    else:
        lines.append("_No calendar entries configured._")
    lines.append("")

    lines.append("## Needs review")
    if review_items:
        for r in review_items:
            lines.append(
                f"- [{r['title']}]({r['url']}) -- source: {r['source_id']}, "
                f"contest score: {r['contest_score']}, grant score: {r['grant_score']}"
            )
    else:
        lines.append("_Nothing pending review._")
    lines.append("")

    lines.append("## Sources needing attention")
    if broken_sources:
        for s in broken_sources:
            err = f" -- {s['last_error']}" if s["last_error"] else ""
            lines.append(f"- **{s['source_id']}**: {s['last_status']} (last count: {s['last_count']}){err}")
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
