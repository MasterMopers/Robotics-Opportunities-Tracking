from ._http import get

TITLE_FIELDS = ("title", "name")
URL_FIELDS = ("url", "link", "html_url", "story_url")
SNIPPET_FIELDS = ("description", "story_text", "_highlightResult")


def _dig(obj, dotted_path):
    cur = obj
    for part in dotted_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _first(d: dict, fields, default=""):
    for f in fields:
        v = d.get(f)
        if v:
            return v
    return default


def fetch(source: dict) -> list:
    resp = get(source["url"], headers=source.get("request_headers"))
    data = resp.json()

    raw_items = data
    path = source.get("json_items_path")
    if path:
        raw_items = _dig(data, path) or []

    keywords = [k.lower() for k in source.get("filter_keywords_any", [])]

    items = []
    for raw in raw_items:
        title = _first(raw, TITLE_FIELDS)
        url = _first(raw, URL_FIELDS)
        if not url and raw.get("objectID"):
            # HN Algolia: text posts have no external url.
            url = f"https://news.ycombinator.com/item?id={raw['objectID']}"
        if not title or not url:
            continue

        themes = raw.get("themes") or []
        theme_names = " ".join(t.get("name", "") for t in themes if isinstance(t, dict))
        snippet = f"{raw.get('story_text') or ''} {theme_names}".strip()

        haystack = f"{title} {snippet}".lower()
        if keywords and not any(k in haystack for k in keywords):
            continue

        items.append({"title": title.strip(), "url": url.strip(), "snippet": snippet[:300]})
    return items
