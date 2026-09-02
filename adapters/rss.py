import feedparser

from ._http import USER_AGENT


def fetch(source: dict) -> list:
    parsed = feedparser.parse(source["url"], agent=USER_AGENT)
    items = []
    for entry in parsed.entries:
        items.append(
            {
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", "").strip(),
                "snippet": entry.get("summary", "").strip(),
            }
        )
    return items
