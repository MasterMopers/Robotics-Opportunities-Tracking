"""Turn a raw HTTP response body into something worth pattern-matching
against.

Before this module existed, every extractor in lib/enrich.py ran regexes
directly against `title + snippet + http_get(url).text` -- the unparsed
HTML response body, script tags, nav/footer boilerplate and all. That is
the single largest cause of empty/garbage enrichment fields: JSON-LD
deadline/location data was buried inside a `<script>` tag the deadline
regexes never looked at (they only look at visible-text-shaped patterns),
and the `_PLACE_JUNK_TOKENS` filter in lib/enrich.py existed purely to
reject markup noise (`aria-`, `svg`, `<`, `=`, ...) picked up by matching
against raw markup in the first place -- a self-inflicted problem, not a
real-world one.

`extract_document()` does the parsing once, up front, and hands back a
`Document` with two independent views of the page:

- `.text`: clean, human-readable prose (scripts/styles/nav/footer/header
  stripped, whitespace collapsed) -- this is what the plain-English regex
  patterns in rules.yaml (`deadline_patterns`, `location_city_patterns`'
  non-JSON-LD entries, `participant_count_patterns`, signal phrases, etc.)
  should run against.
- `.structured`: every JSON-LD (`<script type="application/ld+json">`)
  payload on the page, plus Next.js's `__NEXT_DATA__` and Nuxt's
  `window.__NUXT__` embedded state, each parsed into a plain dict/list and
  flattened into one list. This is where a deadline or an address actually
  lives on JS-rendered platforms like Devpost/Hackster/MLH -- their visible
  HTML is a mostly-empty shell, and the real data ships as one of these
  payloads. Malformed JSON in any of these locations is skipped silently:
  a broken third-party JSON-LD block on someone else's page is not this
  project's problem to raise an exception over, and per the "never guess"
  invariant, skipping it just means that field stays unresolved rather than
  than crashing the whole enrichment pass.
"""

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

_STRIP_TAGS = ("script", "style", "nav", "footer", "header", "noscript")
_WHITESPACE_RUN = re.compile(r"\s+")

# window.__NUXT__={...};  -- optionally followed by more statements on the
# same line, so we take the balanced-looking assignment up to the first
# top-level `;` at end of line/script, falling back to "rest of the script"
# if that heuristic doesn't find one. We don't need to be a JS parser here:
# we just need the JSON object literal that follows `=`.
_NUXT_ASSIGN = re.compile(r"window\.__NUXT__\s*=\s*")


@dataclass
class Document:
    text: str = ""
    structured: list = field(default_factory=list)


def _strip_and_get_text(soup: BeautifulSoup) -> str:
    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    text = soup.get_text(" ", strip=True)
    return _WHITESPACE_RUN.sub(" ", text).strip()


def _safe_json_loads(raw: str):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _collect_json_ld(soup: BeautifulSoup) -> list:
    payloads = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        if not raw or not raw.strip():
            continue
        parsed = _safe_json_loads(raw.strip())
        if parsed is None:
            continue
        if isinstance(parsed, list):
            payloads.extend(p for p in parsed if isinstance(p, dict))
        elif isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _collect_next_data(soup: BeautifulSoup) -> list:
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag is None:
        return []
    raw = tag.string or tag.get_text()
    if not raw or not raw.strip():
        return []
    parsed = _safe_json_loads(raw.strip())
    if isinstance(parsed, dict):
        return [parsed]
    return []


def _collect_nuxt_data(soup: BeautifulSoup) -> list:
    payloads = []
    for tag in soup.find_all("script"):
        raw = tag.string or tag.get_text() or ""
        if "__NUXT__" not in raw:
            continue
        m = _NUXT_ASSIGN.search(raw)
        if not m:
            continue
        candidate = raw[m.end():].strip()
        # Trim a single trailing `;` (and anything after it, e.g. a
        # following statement on the same line) if present -- best-effort,
        # not a JS parser; a malformed/partial capture just fails to parse
        # as JSON below and is skipped.
        if candidate.endswith(";"):
            candidate = candidate[:-1]
        else:
            # window.__NUXT__={...};(function(){...})();  -- cut at the
            # first `};` boundary if a bare trailing `;` wasn't found.
            cut = candidate.find("};")
            if cut != -1:
                candidate = candidate[: cut + 1]
        parsed = _safe_json_loads(candidate)
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


# A small allowlist of known "this element's text is a location, signaled
# by its CSS class, not by surrounding prose" markers (e.g. MLH's contest
# hero banner). This used to be matched by regex directly against raw HTML
# (`lv-hero-location">([^<]{2,60})<`) -- exactly the antipattern this module
# exists to eliminate. Doing it as a structural DOM lookup here and handing
# the result to lib/enrich.py as a plain dict in `.structured` (walked by
# key lookup, same as JSON-LD) keeps this out of the regex-over-markup path
# entirely, without inventing a fact the page doesn't state.
_LOCATION_HINT_CLASSES = ("lv-hero-location",)


def _collect_location_hints(soup: BeautifulSoup) -> list:
    hints = []
    for class_name in _LOCATION_HINT_CLASSES:
        for el in soup.find_all(class_=class_name):
            text = el.get_text(" ", strip=True)
            if text:
                hints.append({"@type": "LocationHint", "text": text})
    return hints


def extract_document(html: str) -> Document:
    """Returns a Document with .text (clean prose) and .structured (list of
    dicts pulled from JSON-LD / __NEXT_DATA__ / window.__NUXT__ / known
    location-hint CSS classes)."""
    if not html:
        return Document(text="", structured=[])

    soup = BeautifulSoup(html, "html.parser")

    structured = []
    structured.extend(_collect_json_ld(soup))
    structured.extend(_collect_next_data(soup))
    structured.extend(_collect_nuxt_data(soup))
    structured.extend(_collect_location_hints(soup))

    text = _strip_and_get_text(soup)

    return Document(text=text, structured=structured)
