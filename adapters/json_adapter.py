from ._http import get

TITLE_FIELDS = ("title", "name")
URL_FIELDS = ("url", "link", "html_url", "story_url")
# Free-text description fields tried, in order, for a generic snippet --
# "promo" is HeroX's one-line challenge description (Phase 4c probe), the
# rest predate it.
SNIPPET_FIELDS = ("story_text", "description", "promo", "_highlightResult")


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


def _extract_items(data, path):
    raw_items = data
    if path:
        raw_items = _dig(data, path) or []
    return raw_items


def fetch(source: dict) -> list:
    path = source.get("json_items_path")
    keywords = [k.lower() for k in source.get("filter_keywords_any", [])]
    # `paginate_next_field`: some APIs (e.g. HeroX's internal search
    # endpoint) have no server-side keyword filter, so a client-side
    # keyword match against only the first page would almost always come
    # back empty -- following a `next` URL field across pages (bounded by
    # `max_pages`, default 10) is what makes that filtering meaningful.
    # Sources with a real server-side filter (Devpost's old json path, HN
    # Algolia) never set this and fetch exactly one page, as before.
    next_field = source.get("paginate_next_field")
    max_pages = source.get("max_pages", 10)

    items = []
    url = source["url"]
    pages_fetched = 0
    while url and pages_fetched < max_pages:
        resp = get(url, headers=source.get("request_headers"))
        data = resp.json()
        pages_fetched += 1

        for raw in _extract_items(data, path):
            title = _first(raw, TITLE_FIELDS)
            item_url = _first(raw, URL_FIELDS)
            if not item_url and raw.get("objectID"):
                # HN Algolia: text posts have no external url.
                item_url = f"https://news.ycombinator.com/item?id={raw['objectID']}"
            if not title or not item_url:
                continue

            themes = raw.get("themes") or []
            theme_names = " ".join(t.get("name", "") for t in themes if isinstance(t, dict))
            description = _first(raw, SNIPPET_FIELDS)
            if not isinstance(description, str):
                description = ""
            snippet = f"{description} {theme_names}".strip()

            haystack = f"{title} {snippet}".lower()
            if keywords and not any(k in haystack for k in keywords):
                continue

            items.append({"title": title.strip(), "url": item_url.strip(), "snippet": snippet[:300]})

        url = data.get(next_field) if next_field else None

    return items
