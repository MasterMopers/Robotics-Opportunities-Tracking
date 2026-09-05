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
from lib import eligibility as eligibility_lib
from lib import enrich as enrich_lib
from lib import llm_enrich
from lib import normalize
from lib.extract import Document, extract_document

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "state.db")
SOURCES_PATH = os.path.join(ROOT, "sources.yaml")
RULES_PATH = os.path.join(ROOT, "rules.yaml")
PROFILE_PATH = os.path.join(ROOT, "profile.yaml")


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


def needs_reenrichment(existing_row, today=None) -> bool:
    """Phase 5: replaces the old "if it exists, only touch last_seen"
    short-circuit, which meant a row enriched from a JS shell on day one
    (or simply never enriched because it predates a given extractor) stayed
    Unknown forever. Re-run the full pipeline on an existing row whenever
    any of:
      - deadline_confidence = 'none' (never resolved)
      - relevance_score IS NULL (predates the relevance axis, or was never
        scored for some other reason)
      - enriched = 0 (explicitly marked as not really enriched -- e.g. the
        JS-shell case from Phase 1)
      - last_enriched is more than 14 days old (routine refresh -- a page
        that was a JS shell when first crawled may not be one anymore, a
        rolling deadline may have been posted since, etc.)
    `--backfill` remains a manual escape hatch (lib/db.py docstring), but
    stops being the only repair mechanism."""
    today = today or datetime.now(timezone.utc)

    if existing_row["deadline_confidence"] in (None, "none"):
        return True
    if existing_row["relevance_score"] is None:
        return True
    if not existing_row["enriched"]:
        return True

    last_enriched = existing_row["last_enriched"]
    if not last_enriched:
        return True
    try:
        last = datetime.fromisoformat(last_enriched)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (today - last) > timedelta(days=14)


def process_item(conn, source, raw, rules, init_mode, report, llm_budget, profile):
    if not raw.get("url") or not raw.get("title"):
        return
    iid = normalize.item_id(raw["url"])
    existing = conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone()
    ts = now_iso()

    if existing and not needs_reenrichment(existing):
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

    # lib/extract.py does the HTML parsing once: doc.text is clean prose
    # (scripts/nav/footer/etc. stripped), doc.structured is every JSON-LD /
    # __NEXT_DATA__ / __NUXT__ / location-hint payload on the page. Title
    # and snippet are folded into the text view (they're never markup), but
    # never into .structured -- only the fetched page can carry that.
    page_doc = extract_document(page_text)
    combined_doc = Document(
        text=f"{raw['title']} {snippet} {page_doc.text}".strip(),
        structured=page_doc.structured,
    )

    # Devpost/Hackster/MLH-style detail pages ship a near-empty HTML shell
    # plus a JS-rendered payload; if neither clean text nor any structured
    # payload came through, this is a half-parsed shell, not real content.
    # Store it as unenriched (every enrichment field NULL) rather than a
    # row that looks resolved but is really just noise -- Phase 5's
    # re-enrichment pass retries any row left at enriched=0.
    is_js_shell = len(page_doc.text) < 500 and not page_doc.structured

    enrichment = enrich_lib.enrich_item(combined_doc, rules)

    # Some adapters (devpost_api, bulk_xml) supply already-resolved facts
    # straight from a structured API/export -- e.g. Devpost's own
    # submission_period_dates is more authoritative than anything a regex
    # could recover from that hackathon's own rendered page. Apply those as
    # an override, but only where the adapter actually resolved something
    # (confidence != "none") -- an adapter that couldn't determine a field
    # must never blank out a real result the deterministic extractors found
    # from the page text.
    prefilled = raw.get("prefilled")
    if prefilled:
        enrichment = dict(enrichment)
        for value_key, conf_key in (
            ("deadline_date", "deadline_confidence"),
            ("location", "location_confidence"),
            ("participants_count", "participants_confidence"),
        ):
            if prefilled.get(conf_key, "none") == "none":
                continue
            enrichment[value_key] = prefilled[value_key]
            enrichment[conf_key] = prefilled[conf_key]
            if value_key == "location":
                enrichment["location_format"] = prefilled.get("location_format", enrichment.get("location_format"))
        if prefilled.get("money_raw") is not None:
            enrichment["money_raw"] = prefilled["money_raw"]

    decision = classify_lib.classify_item(source["class"], source["trust"], enrichment, rules)

    if source["method"] == "github":
        # A commit touching a watched file is a signal that the list
        # changed, not itself a submittable opportunity (its "title" is a
        # commit message, not a grant name) -- always route to review so it
        # surfaces without being misrepresented as a live grant row.
        decision = {"status": "review", "final_class": None, "reject_phrase": None}

    # LLM fallback runs strictly after classification -- it can only ever
    # touch enrichment["location"/"participants_count"/"deadline_date"],
    # never scores/reject, so it structurally cannot influence
    # accept/review/reject either way.
    enrichment = llm_enrich.apply_llm_fallback(enrichment, combined_doc.text, llm_budget)

    enriched_flag = 1
    if is_js_shell:
        enrichment = dict(enrichment)
        enrichment.update(
            {
                "deadline_date": None, "deadline_confidence": "none",
                "money_raw": None, "team_size": None,
                "location": None, "location_format": "Unknown", "location_confidence": "none",
                "participants_count": None, "participants_confidence": "none",
            }
        )
        enriched_flag = 0

    eligibility_verdict, eligibility_reason = eligibility_lib.evaluate(enrichment, profile)

    # Shared between the INSERT (brand-new item) and UPDATE (Phase 5
    # re-enrichment of an existing row) paths -- everything except the
    # identity/bookkeeping columns (id, first_seen, last_seen,
    # last_enriched, reported), which are handled per-path below.
    enrichment_columns = [
        "source_id", "class", "title", "url", "snippet", "status",
        "final_class", "contest_score", "grant_score", "matched_signals", "reject_phrase",
        "deadline_date", "deadline_confidence", "money_raw", "team_size",
        "location", "location_format", "location_confidence",
        "participants_count", "participants_confidence",
        "relevance_score", "relevance_core_hits", "relevance_buckets", "relevance_terms",
        "requires_incorporation", "requires_incorporation_confidence",
        "requires_faculty_sponsor", "requires_faculty_sponsor_confidence",
        "requires_us_person", "requires_us_person_confidence",
        "min_age", "min_age_confidence", "max_age", "max_age_confidence",
        "entry_fee_usd", "entry_fee_usd_confidence",
        "max_team_size", "max_team_size_confidence",
        "requires_enrollment", "requires_enrollment_confidence",
        "equity_required", "equity_required_confidence",
        "eligibility", "enriched",
    ]
    enrichment_values = (
        source["id"], source["class"], raw["title"], raw["url"], snippet, decision["status"],
        decision["final_class"], enrichment["scores"]["contest"], enrichment["scores"]["grant"],
        json.dumps(enrichment["matched_signals"]), decision["reject_phrase"],
        enrichment["deadline_date"], enrichment["deadline_confidence"],
        enrichment["money_raw"], enrichment["team_size"],
        enrichment["location"], enrichment["location_format"], enrichment["location_confidence"],
        enrichment["participants_count"], enrichment["participants_confidence"],
        enrichment["relevance_score"], enrichment["relevance_core_hits"],
        json.dumps(enrichment["relevance_buckets"]), json.dumps(enrichment["relevance_terms"]),
        enrichment["requires_incorporation"], enrichment["requires_incorporation_confidence"],
        enrichment["requires_faculty_sponsor"], enrichment["requires_faculty_sponsor_confidence"],
        enrichment["requires_us_person"], enrichment["requires_us_person_confidence"],
        enrichment["min_age"], enrichment["min_age_confidence"],
        enrichment["max_age"], enrichment["max_age_confidence"],
        enrichment["entry_fee_usd"], enrichment["entry_fee_usd_confidence"],
        enrichment["max_team_size"], enrichment["max_team_size_confidence"],
        enrichment["requires_enrollment"], enrichment["requires_enrollment_confidence"],
        enrichment["equity_required"], enrichment["equity_required_confidence"],
        eligibility_verdict, enriched_flag,
    )
    assert len(enrichment_columns) == len(enrichment_values), (
        f"{len(enrichment_columns)} columns vs {len(enrichment_values)} values"
    )

    if existing:
        # Re-enrichment of a row that already exists (Phase 5): overwrite
        # everything the pipeline just recomputed, including status/
        # final_class (a row can be reclassified once a gap is filled --
        # that's the point), but never first_seen, and never reported
        # (re-enrichment is not "this is new," so it must not trigger a
        # duplicate GitHub issue mention for an item already reported).
        set_clause = ", ".join(f"{c} = ?" for c in enrichment_columns)
        conn.execute(
            f"UPDATE items SET {set_clause}, last_seen = ?, last_enriched = ? WHERE id = ?",
            enrichment_values + (ts, ts, iid),
        )
        return

    columns = ["id"] + enrichment_columns + ["first_seen", "last_seen", "last_enriched", "reported"]
    values = (iid,) + enrichment_values + (ts, ts, ts, 1 if init_mode else 0)
    placeholders = ",".join("?" * len(columns))
    conn.execute(f"INSERT INTO items ({','.join(columns)}) VALUES ({placeholders})", values)

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


def run_source(conn, source, rules, init_mode, report, llm_budget, profile):
    source_id = source["id"]

    if source.get("disabled"):
        # Phase 4c: a source that failed its Phase 0b/4 probe (no live
        # feed/endpoint found) is marked `disabled: true` with a reason in
        # sources.yaml rather than deleted -- never fetched, never counted
        # as BROKEN/ERROR (it isn't failing, it's intentionally off), but
        # still visible in the source report for auditability.
        conn.execute(
            """INSERT INTO source_health (source_id, last_run, last_status, last_count, last_error)
               VALUES (?, ?, 'DISABLED', 0, ?)
               ON CONFLICT(source_id) DO UPDATE SET
                 last_run=excluded.last_run, last_status=excluded.last_status,
                 last_count=excluded.last_count, last_error=excluded.last_error""",
            (source_id, now_iso(), source.get("disabled_reason")),
        )
        report["sources"].append(
            {
                "id": source_id,
                "name": source["name"],
                "class": source["class"],
                "trust": source["trust"],
                "method": source["method"],
                "count": 0,
                "status": "DISABLED",
                "error": source.get("disabled_reason"),
            }
        )
        return

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
        process_item(conn, source, raw, rules, init_mode, report, llm_budget, profile)


def backfill_location_participants(conn, rules, llm_budget):
    """One-time pass: re-fetch each currently accepted item's own page once
    to fill in location/participants/deadline for rows still unresolved --
    regex/JSON-LD first, then the same opt-in LLM fallback used by the
    normal run for whatever that still can't find. Not part of the
    recurring weekly/monthly jobs -- those already enrich every new diff
    item for these fields at no extra request cost, via the same page
    fetch process_item always did."""
    rows = conn.execute(
        "SELECT id, url, title, snippet FROM items WHERE status='accepted' "
        "AND (location_confidence IS NULL OR location_confidence='none' "
        "OR participants_confidence IS NULL OR participants_confidence='none' "
        "OR deadline_confidence IS NULL OR deadline_confidence='none')"
    ).fetchall()
    updated, failed, llm_used = 0, 0, 0
    for r in rows:
        try:
            page_text = http_get(r["url"]).text
        except Exception as e:
            print(f"  backfill fetch failed for {r['url']}: {type(e).__name__}: {e}", file=sys.stderr)
            failed += 1
            continue
        page_doc = extract_document(page_text)
        combined_doc = Document(
            text=f"{r['title']} {r['snippet'] or ''} {page_doc.text}".strip(),
            structured=page_doc.structured,
        )
        combined = combined_doc.text
        location, fmt, loc_conf = enrich_lib.extract_location(combined_doc, rules)
        count, count_conf = enrich_lib.extract_participants(combined_doc, rules)
        deadline_date, deadline_conf = enrich_lib.extract_deadline(combined_doc, rules)
        enrichment = {
            "location": location, "location_format": fmt, "location_confidence": loc_conf,
            "participants_count": count, "participants_confidence": count_conf,
            "deadline_date": deadline_date, "deadline_confidence": deadline_conf,
        }
        before = (loc_conf, count_conf, deadline_conf)
        enrichment = llm_enrich.apply_llm_fallback(enrichment, combined, llm_budget)
        if (enrichment["location_confidence"], enrichment["participants_confidence"],
                enrichment["deadline_confidence"]) != before:
            llm_used += 1
        conn.execute(
            """UPDATE items SET location=?, location_format=?, location_confidence=?,
               participants_count=?, participants_confidence=?,
               deadline_date=?, deadline_confidence=? WHERE id=?""",
            (enrichment["location"], enrichment["location_format"], enrichment["location_confidence"],
             enrichment["participants_count"], enrichment["participants_confidence"],
             enrichment["deadline_date"], enrichment["deadline_confidence"], r["id"]),
        )
        updated += 1
    print(f"Backfill: {updated} item(s) updated ({llm_used} via LLM fallback), {failed} fetch failure(s).")


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

        if not already_alerted:
            report["calendar_alerts"].append(
                {
                    "name": entry["name"],
                    "target_date": target.isoformat(),
                    "days_until": days_until,
                    "lead_days": entry["lead_days"],
                    "confirmed": entry["confirmed"],
                    "link": entry["link"],
                }
            )

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
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="One-time: re-fetch accepted items missing location/participants fields, then exit. "
             "Not run by the recurring workflow.",
    )
    parser.add_argument(
        "--cadence",
        choices=("daily", "weekly"),
        default=None,
        help="Phase 5: only run sources whose sources.yaml `cadence` matches (API-shaped sources are "
             "daily, HTML scrapers are weekly -- absent cadence defaults to weekly). Omit to run every "
             "source regardless of cadence (used by --init and any ad-hoc manual run).",
    )
    args = parser.parse_args()

    sources_cfg = load_yaml(SOURCES_PATH)
    rules = load_yaml(RULES_PATH)
    profile = load_yaml(PROFILE_PATH)["profile"]

    db.init_db(DB_PATH)

    if args.backfill:
        llm_budget = llm_enrich.LLMCallBudget(llm_enrich.MAX_LLM_CALLS_PER_BACKFILL)
        with db.connect(DB_PATH) as conn:
            backfill_location_participants(conn, rules, llm_budget)
        return

    llm_budget = llm_enrich.LLMCallBudget(llm_enrich.MAX_LLM_CALLS_PER_RUN)
    report = new_report()

    sources_to_run = sources_cfg["sources"]
    if args.cadence:
        sources_to_run = [s for s in sources_to_run if s.get("cadence", "weekly") == args.cadence]

    with db.connect(DB_PATH) as conn:
        for source in sources_to_run:
            run_source(conn, source, rules, args.init, report, llm_budget, profile)

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
