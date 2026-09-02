import json

from bs4 import BeautifulSoup

from ._http import get


def _dig(obj, dotted_path):
    cur = obj
    for part in dotted_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def fetch(source: dict) -> list:
    resp = get(source["url"], headers=source.get("request_headers"))
    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.select_one(source["embedded_script_selector"])
    if script is None or not script.string:
        raise ValueError(f"json_embedded adapter: script not found for {source['id']}")

    data = json.loads(script.string)
    events = _dig(data, source["json_path"]) or []

    items = []
    for ev in events:
        title = ev.get("name") or ev.get("title") or ""
        url = ev.get("websiteUrl") or ev.get("url") or ""
        if not title or not url:
            continue
        snippet = f"{ev.get('dateRange', '')} {ev.get('location', '')}".strip()
        items.append({"title": title.strip(), "url": url.strip(), "snippet": snippet})
    return items
