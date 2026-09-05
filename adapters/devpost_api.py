"""Phase 4b: Devpost's real hackathons API, replacing the old
generic-json + client-side-keyword-filter source.

Phase 0b live-probed `https://devpost.com/api/hackathons` (see
docs/PROBES.md): `search=`, `status[]=`, `page=`, and `per_page=` all
filter SERVER-SIDE and compose correctly -- the WAF-403 note that used to
justify client-side-only filtering did not reproduce. So this adapter
queries the API with a small set of robotics-ish search terms and
`status[]=open`/`status[]=upcoming`, instead of paginating the full
~14,000-hackathon unfiltered set and filtering after the fact.

It also maps fields directly onto enrichment columns instead of regexing
them out of a fetched page -- this is the largest source, and this
supplies deadline/location/participants/money for it with no regex and no
LLM call:

| Devpost field | Column | confidence |
|---|---|---|
| `submission_period_dates` | `deadline_date` (end of the range) | explicit |
| `displayed_location.location` | `location`/`location_format` ("Online" -> Remote) | explicit/inferred |
| `registrations_count` | `participants_count` | explicit |
| `prize_amount` | `money_raw` (strips the `<span data-currency-value>` wrapper) | n/a (money_raw has no confidence column) |

monitor.py merges these into the normal enrichment dict as a `prefilled`
override (applied after the deterministic page-text extractors, since an
API's own structured field is more authoritative than a regex over
whatever HTML the hackathon's own page happens to render) -- see
process_item's handling of raw["prefilled"].

`themes[].name` is folded into the snippet (not prefilled) so it feeds the
relevance scorer/signals alongside the title, exactly like any other
title+snippet text would.
"""

import re

from dateutil import parser as dateutil_parser

from ._http import get

API_URL = "https://devpost.com/api/hackathons"
DEFAULT_HEADERS = {"Referer": "https://devpost.com/hackathons", "Accept": "application/json"}

# A small set of robotics-ish search terms, queried individually against
# the CONFIRMED-live server-side `search=` param (Phase 0b) and merged/
# deduped by hackathon id. Kept as a rules-adjacent constant here (adapter
# wiring, not classification tuning) rather than in rules.yaml, since this
# is "which API queries to issue," not a scoring/matching rule.
SEARCH_TERMS = ("robot", "robotics", "hardware", "embedded", "drone", "pcb", "firmware")
STATUSES = ("open", "upcoming")

# Defensive client-side fallback, kept per the spec even though the Phase
# 0b probe found server-side filtering reliable -- costs nothing if the
# server-side filter already narrowed the set, and protects against the
# WAF-403 behavior described in the old sources.yaml notes returning
# intermittently.
FALLBACK_KEYWORDS = ("robot", "robotics", "hardware", "embedded", "iot", "firmware", "maker ", "drone", "pcb")

_CURRENCY_SPAN = re.compile(r"<[^>]+>")
_MONTH_WORD = re.compile(r"[A-Za-z]{3,9}")


def _strip_money_markup(prize_amount):
    if not prize_amount:
        return None
    cleaned = _CURRENCY_SPAN.sub("", prize_amount).strip()
    return cleaned or None


def parse_submission_deadline(dates_str):
    """"May 22 - Sep 13, 2026" -> ("2026-09-13", "explicit"); "Sep 10 - 13,
    2026" -> borrows the month from the first segment for the second, since
    the end segment alone ("13, 2026") has no month for dateutil to parse.
    Returns (None, "none") for anything that doesn't parse -- never a
    guessed date."""
    if not dates_str or not dates_str.strip():
        return None, "none"
    parts = re.split(r"\s*-\s*|\s*–\s*", dates_str.strip())
    end = parts[-1].strip()
    if end and not _MONTH_WORD.search(end) and len(parts) > 1:
        first_month = _MONTH_WORD.search(parts[0])
        if first_month:
            end = f"{first_month.group(0)} {end}"
    try:
        parsed = dateutil_parser.parse(end, fuzzy=True, default=None)
    except (ValueError, OverflowError, TypeError):
        return None, "none"
    return parsed.date().isoformat(), "explicit"


def _map_location(displayed_location):
    place = (displayed_location or {}).get("location") if isinstance(displayed_location, dict) else None
    if not place:
        return None, "Unknown", "none"
    if place.strip().lower() == "online":
        return None, "Remote", "inferred"
    return place.strip(), "In-person", "explicit"


def map_hackathon(h: dict) -> dict:
    """Maps one raw Devpost hackathon record to the standard {title, url,
    snippet} item shape plus a `prefilled` block of already-resolved
    enrichment fields monitor.py merges in ahead of the deterministic
    page-text extractors."""
    title = (h.get("title") or "").strip()
    url = (h.get("url") or "").strip()

    themes = h.get("themes") or []
    theme_names = " ".join(t.get("name", "") for t in themes if isinstance(t, dict))
    org = h.get("organization_name") or ""
    snippet = f"{theme_names} {org}".strip()

    deadline_date, deadline_confidence = parse_submission_deadline(h.get("submission_period_dates"))
    location, location_format, location_confidence = _map_location(h.get("displayed_location"))
    registrations_count = h.get("registrations_count")
    participants_confidence = "explicit" if registrations_count is not None else "none"
    money_raw = _strip_money_markup(h.get("prize_amount"))

    prefilled = {
        "deadline_date": deadline_date,
        "deadline_confidence": deadline_confidence,
        "location": location,
        "location_format": location_format,
        "location_confidence": location_confidence,
        "participants_count": registrations_count,
        "participants_confidence": participants_confidence,
        "money_raw": money_raw,
    }

    return {"title": title, "url": url, "snippet": snippet, "prefilled": prefilled}


def fetch(source: dict) -> list:
    seen_urls = set()
    items = []

    for term in SEARCH_TERMS:
        for status in STATUSES:
            params = f"search={term}&status[]={status}&per_page=50"
            url = f"{API_URL}?{params}"
            try:
                resp = get(url, headers=DEFAULT_HEADERS)
                data = resp.json()
            except Exception:
                # One search/status combo failing (WAF hiccup, timeout) is
                # not fatal to the whole source -- the other combos still
                # run, and min_expected/BROKEN catches a total wipeout.
                continue

            for h in data.get("hackathons", []):
                if h.get("open_state") not in ("open", "upcoming"):
                    continue
                raw_url = (h.get("url") or "").strip()
                if not raw_url or raw_url in seen_urls:
                    continue
                seen_urls.add(raw_url)
                mapped = map_hackathon(h)
                if not mapped["title"] or not mapped["url"]:
                    continue
                items.append(mapped)

    return items
