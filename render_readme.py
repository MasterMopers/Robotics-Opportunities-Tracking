#!/usr/bin/env python3
"""Rewrites only the block between the autogen markers in README.md, from
state.db. Everything outside that block is hand-written and survives."""

import os
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

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


def render_contest_row(item):
    deadline = item["deadline_date"] or "rolling/unknown"
    team = item["team_size"] or "n/a"
    money = item["money_raw"] or "n/a"
    return (
        f"| [{esc(item['title'])}]({item['url']}) | {esc(money)} | {esc(team)} | "
        f"{location_cell(item)} | {participants_cell(item)} | {deadline} |"
    )


def render_grant_row(item):
    money = item["money_raw"] or "amount not extracted"
    deadline = item["deadline_date"] or "rolling"
    return (
        f"| [{esc(item['title'])}]({item['url']}) | {esc(money)} | {location_cell(item)} | {deadline} |"
    )


def build_autogen_block(conn):
    today = date.today()
    now_est = datetime.now(ZoneInfo("America/New_York"))
    now = now_est.strftime("%Y-%m-%d %H:%M %Z")

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

    with_location = sum(1 for c in list(contests) + list(grants) if c["location_format"] and c["location_format"] != "Unknown")
    with_count = sum(1 for c in list(contests) + list(grants) if c["participants_count"] is not None)
    total_items = len(contests) + len(grants)

    lines = [BEGIN_MARKER, ""]
    lines.append(f"### 📡 {len(contests)} open contest(s) · {len(grants)} grant(s) listed · updated {now}")
    lines.append("")
    if total_items:
        lines.append(
            f"_Format (in-person/remote/hybrid) is known for {with_location}/{total_items} listings below; "
            f"a participant count is known for {with_count}/{total_items} -- both only ever come from what the "
            f"source page itself states, never a guess. \"Unknown\" means the page didn't say._"
        )
        lines.append("")

    lines.append("## Open contests")
    lines.append(
        "_Sorted by deadline. \"Team size\" and \"Format\" come straight from each contest's own page._"
    )
    lines.append("")
    if contests:
        rows = [render_contest_row(c) for c in contests]
        lines.append(table(["Contest", "Prize", "Team size", "Format", "Participants (last count)", "Deadline"], rows))
    else:
        lines.append("_None currently open._")
    lines.append("")

    lines.append("## Available grants")
    lines.append("_Sorted by amount (highest first). Most microgrant programs are rolling, not deadline-based._")
    lines.append("")
    if grants:
        rows = [render_grant_row(g) for g in grants]
        lines.append(table(["Grant", "Amount", "Format / eligibility area", "Deadline"], rows))
    else:
        lines.append("_None currently listed._")
    lines.append("")

    lines.append("## Closing soon")
    lines.append(f"_Contests inside {CLOSING_SOON_DAYS} days of their deadline._")
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
        lines.append("_Nothing pending review._")
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
