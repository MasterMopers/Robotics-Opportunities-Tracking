#!/usr/bin/env python3
"""Discovery pipeline: sources.yaml -> fetch -> parse -> normalize ->
diff(SQLite) -> enrich -> classify -> report.

Also runs the Watch calendar check (annual/monthly programs tracked by
date + lead_days instead of scraping).

Usage:
    python monitor.py --init          # seed state.db, no notification
    python monitor.py                 # normal run: diff, enrich, classify,
                                       #   report (opens a GitHub issue when
                                       #   run inside GitHub Actions and there
                                       #   is something new to report)
    python monitor.py --show-rejects  # also print the full reject list
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import yaml

from adapters import fetch_source
from adapters._http import get as http_get
from lib import classify as classify_lib
from lib import db
from lib import enrich as enrich_lib
from lib import normalize

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "state.db")
SOURCES_PATH = os.path.join(ROOT, "sources.yaml")
RULES_PATH = os.path.join(ROOT, "rules.yaml")


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def new_report():
    return {
        "sources": [],
        "broken": [],
        "new_accepted": [],
        "new_review": [],
        "new_rejected": [],
        "calendar_alerts": [],
    }


def process_item(conn, source, raw, rules, init_mode, report):
    if not raw.get("url") or not raw.get("title"):
        return
    iid = normalize.item_id(raw["url"])
    existing = conn.execute("SELECT id FROM items WHERE id = ?", (iid,)).fetchone()
    ts = now_iso()

    if existing:
        conn.execute(
            "UPDATE items SET last_seen = ?, title = ? WHERE id = ?",
            (ts, raw["title"], iid),
        )
        return

    snippet = raw.get("snippet", "") or ""
    page_text = ""
    try:
        page_text = http_get(raw["url"]).text
    except Exception:
        pass  # enrichment is best-effort; classification still runs off title+snippet

    combined_text = f"{raw['title']} {snippet} {page_text}"
    enrichment = enrich_lib.enrich_item(combined_text, rules)
    decision = classify_lib.classify_item(source["class"], source["trust"], enrichment, rules)

    if source["method"] == "github":
        # A commit touching a watched file is a signal that the list
        # changed, not itself a submittable opportunity (its "title" is a
        # commit message, not a grant name) -- always route to review so it
        # surfaces without being misrepresented as a live grant row.
        decision = {"status": "review", "final_class": None, "reject_phrase": None}

    conn.execute(
        """INSERT INTO items
           (id, source_id, class, title, url, snippet, first_seen, last_seen, status,
            final_class, contest_score, grant_score, matched_signals, reject_phrase,
            deadline_date, deadline_confidence, money_raw, team_size, enriched, reported)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (
            iid, source["id"], source["class"], raw["title"], raw["url"], snippet, ts, ts,
            decision["status"], decision["final_class"],
            enrichment["scores"]["contest"], enrichment["scores"]["grant"],
            json.dumps(enrichment["matched_signals"]), decision["reject_phrase"],
            enrichment["deadline_date"], enrichment["deadline_confidence"],
            enrichment["money_raw"], enrichment["team_size"],
            1 if init_mode else 0,
        ),
    )

    row = {
        "title": raw["title"],
        "url": raw["url"],
        "source": source["name"],
        "final_class": decision["final_class"],
        "contest_score": enrichment["scores"]["contest"],
        "grant_score": enrichment["scores"]["grant"],
        "reject_phrase": decision["reject_phrase"],
    }
    {"accepted": report["new_accepted"], "review": report["new_review"], "rejected": report["new_rejected"]}[
        decision["status"]
    ].append(row)


def run_source(conn, source, rules, init_mode, report):
    source_id = source["id"]
    error = None
    try:
        raw_items = fetch_source(source)
        status = "OK"
    except Exception as e:
        raw_items = []
        status = "ERROR"
        error = f"{type(e).__name__}: {e}"

    count = len(raw_items)
    min_expected = source.get("min_expected", 0)
    if status == "OK" and count < min_expected:
        status = "BROKEN"

    conn.execute(
        """INSERT INTO source_health (source_id, last_run, last_status, last_count, last_error)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET
             last_run=excluded.last_run, last_status=excluded.last_status,
             last_count=excluded.last_count, last_error=excluded.last_error""",
        (source_id, now_iso(), status, count, error),
    )

    report["sources"].append(
        {
            "id": source_id,
            "name": source["name"],
            "class": source["class"],
            "trust": source["trust"],
            "method": source["method"],
            "count": count,
            "status": status,
            "error": error,
        }
    )
    if status in ("BROKEN", "ERROR"):
        report["broken"].append(
            {"id": source_id, "name": source["name"], "count": count, "min_expected": min_expected, "error": error}
        )

    for raw in raw_items:
        process_item(conn, source, raw, rules, init_mode, report)


def _next_occurrence(month, day, today, monthly=False):
    def safe_date(y, m, d):
        # Clamp to the last valid day of the month rather than raising.
        while d > 28:
            try:
                return date(y, m, d)
            except ValueError:
                d -= 1
        return date(y, m, d)

    if monthly:
        candidate = safe_date(today.year, today.month, day)
        if candidate < today:
            y, m = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)
            candidate = safe_date(y, m, day)
        return candidate

    candidate = safe_date(today.year, month, day)
    if candidate < today:
        candidate = safe_date(today.year + 1, month, day)
    return candidate


def check_calendar(conn, calendar_entries, report, today=None):
    today = today or date.today()
    for entry in calendar_entries:
        monthly = entry["month"] == 0
        target = _next_occurrence(entry["month"], entry["day"], today, monthly=monthly)
        days_until = (target - today).days
        if days_until > entry["lead_days"]:
            continue

        row = conn.execute(
            "SELECT last_alerted_for FROM calendar_state WHERE id = ?", (entry["id"],)
        ).fetchone()
        already_alerted = row is not None and row["last_alerted_for"] == target.isoformat()

        alert = {
            "name": entry["name"],
            "target_date": target.isoformat(),
            "days_until": days_until,
            "lead_days": entry["lead_days"],
            "confirmed": entry["confirmed"],
            "link": entry["link"],
            "already_alerted": already_alerted,
        }
        report["calendar_alerts"].append(alert)

        conn.execute(
            "INSERT INTO calendar_state (id, last_alerted_for) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_alerted_for=excluded.last_alerted_for",
            (entry["id"], target.isoformat()),
        )


def expire_stale_items(conn):
    """Contests past their deadline drop off the README (status='expired')
    but the row is retained. Grants are rolling and never auto-expire."""
    today_iso = date.today().isoformat()
    conn.execute(
        """UPDATE items SET status = 'expired'
           WHERE final_class = 'contest' AND status = 'accepted'
             AND deadline_date IS NOT NULL AND deadline_date < ?""",
        (today_iso,),
    )


def format_report(report, calendar_entries):
    lines = []
    lines.append(f"## Robotics Opportunities Tracker -- run {now_iso()}\n")

    lines.append("### Sources")
    for s in report["sources"]:
        flag = "" if s["status"] == "OK" else f"  **{s['status']}**"
        lines.append(f"- {s['name']} ({s['method']}, trust:{s['trust']}): {s['count']} items{flag}")

    if report["broken"]:
        lines.append("\n### Sources needing attention")
        for b in report["broken"]:
            extra = f" -- {b['error']}" if b.get("error") else ""
            lines.append(f"- **{b['name']}**: got {b['count']}, expected >= {b['min_expected']}{extra}")

    if report["new_accepted"]:
        contests = [r for r in report["new_accepted"] if r["final_class"] == "contest"]
        grants = [r for r in report["new_accepted"] if r["final_class"] == "grant"]
        if contests:
            lines.append("\n### New contests")
            for r in contests:
                lines.append(f"- [{r['title']}]({r['url']}) -- {r['source']}")
        if grants:
            lines.append("\n### New grants")
            for r in grants:
                lines.append(f"- [{r['title']}]({r['url']}) -- {r['source']}")

    if report["new_review"]:
        lines.append("\n### Needs review")
        for r in report["new_review"]:
            lines.append(
                f"- [{r['title']}]({r['url']}) -- {r['source']} "
                f"(contest={r['contest_score']}, grant={r['grant_score']})"
            )

    rejected_count = len(report["new_rejected"])
    lines.append(f"\n### Rejected this run: {rejected_count}")

    if report["calendar_alerts"]:
        lines.append("\n### Closing soon (watch calendar)")
        for a in report["calendar_alerts"]:
            unconfirmed = "" if a["confirmed"] else " (date unconfirmed)"
            lines.append(
                f"- {a['name']}: {a['target_date']} -- {a['days_until']} days away{unconfirmed} -- {a['link']}"
            )

    return "\n".join(lines)


def maybe_open_issue(body, report):
    has_news = bool(
        report["new_accepted"] or report["new_review"] or report["broken"] or report["calendar_alerts"]
    )
    if not has_news:
        print("Nothing new to report; skipping issue.")
        return
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("Not running in GitHub Actions; skipping issue creation (report printed above).")
        return

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GITHUB_TOKEN/GITHUB_REPOSITORY not set; skipping issue creation.", file=sys.stderr)
        return

    import requests

    title = f"Robotics opportunities update -- {date.today().isoformat()}"
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"title": title, "body": body},
        timeout=20,
    )
    resp.raise_for_status()
    print(f"Opened issue: {resp.json().get('html_url')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true", help="Seed state.db without notifying.")
    parser.add_argument("--show-rejects", action="store_true", help="Print the full reject list.")
    args = parser.parse_args()

    sources_cfg = load_yaml(SOURCES_PATH)
    rules = load_yaml(RULES_PATH)

    db.init_db(DB_PATH)
    report = new_report()

    with db.connect(DB_PATH) as conn:
        for source in sources_cfg["sources"]:
            run_source(conn, source, rules, args.init, report)

        check_calendar(conn, sources_cfg.get("calendar", []), report)
        expire_stale_items(conn)

        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_run', ?)", (now_iso(),))

        body = format_report(report, sources_cfg.get("calendar", []))
        print(body)

        if args.show_rejects:
            print("\n### Full reject list (this run)")
            for r in report["new_rejected"]:
                print(f"- [{r['title']}]({r['url']}) -- {r['reject_phrase']}")

        if args.init:
            print("\n--init: state seeded, no notification sent.")
        else:
            maybe_open_issue(body, report)
            conn.execute("UPDATE items SET reported = 1 WHERE reported = 0")

    broken_count = len(report["broken"])
    if broken_count:
        print(f"\n{broken_count} source(s) BROKEN or ERROR this run.", file=sys.stderr)


if __name__ == "__main__":
    main()
