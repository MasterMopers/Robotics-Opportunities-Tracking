from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ._http import get


def fetch(source: dict) -> list:
    resp = get(source["url"], headers=source.get("request_headers"))
    soup = BeautifulSoup(resp.text, "html.parser")
    parse_cfg = source.get("parse", {})
    base = source["url"]

    if "link_href_prefix" in parse_cfg:
        return _fetch_by_href_prefix(soup, base, parse_cfg)
    if "item_container_class" in parse_cfg:
        return _fetch_by_container_class(soup, base, parse_cfg)
    if "row_selector" in parse_cfg:
        return _fetch_by_rows(soup, base, parse_cfg)
    if "item_selector" in parse_cfg:
        return _fetch_by_item_selector(soup, base, parse_cfg)

    raise ValueError(f"html adapter: no known parse strategy for {source['id']}")


def _fetch_by_href_prefix(soup, base, cfg):
    prefix = cfg["link_href_prefix"]
    exclude = cfg.get("exclude_href_contains", [])
    seen = set()
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith(prefix):
            continue
        if any(x in href for x in exclude):
            continue
        slug = href[len(prefix):].split("#")[0].split("?")[0]
        if not slug or slug in seen:
            continue
        seen.add(slug)
        title = a.get_text(strip=True) or a.get("aria-label") or slug.replace("-", " ").title()
        items.append({"title": title, "url": urljoin(base, href), "snippet": ""})
    return items


def _fetch_by_container_class(soup, base, cfg):
    container_class = cfg["item_container_class"]
    link_selector = cfg.get("link_selector", "a")
    items = []
    for container in soup.find_all(class_=container_class):
        link = container.select_one(link_selector)
        if not link or not link.get("href"):
            continue
        img = link.find("img")
        # An image-link's own text often includes unrelated hover/blurb
        # markup nested inside the same <a> -- prefer the image alt text,
        # which carries the real title, before falling back to link text.
        title = (img.get("alt") if img is not None else None) or link.get("title") or link.get_text(strip=True)
        title = (title or "").strip()
        if len(title) > 140:
            title = title[:137].rstrip() + "..."
        items.append(
            {
                "title": title,
                "url": urljoin(base, link["href"]),
                "snippet": container.get_text(" ", strip=True)[:300],
            }
        )
    return items


def _fetch_by_rows(soup, base, cfg):
    rows = soup.select(cfg["row_selector"])
    items = []
    for row in rows:
        name_el = row.select_one(cfg["name_selector"])
        link_el = row.select_one(cfg["link_selector"])
        if not name_el or not link_el or not link_el.get("href"):
            continue
        # The name span can wrap a nested "badge" span (e.g. an "OURS" tag);
        # only the direct text belongs in the title.
        direct_text = "".join(
            child for child in name_el.find_all(string=True, recursive=False)
        ).strip()
        name_text = direct_text or name_el.get_text(strip=True)
        cells = row.find_all("td")
        snippet_parts = []
        for idx_key in ("amount_cell_index", "eligibility_cell_index"):
            idx = cfg.get(idx_key)
            if idx is not None and idx < len(cells):
                snippet_parts.append(cells[idx].get_text(" ", strip=True))
        items.append(
            {
                "title": name_text,
                "url": urljoin(base, link_el["href"]),
                "snippet": " | ".join(snippet_parts),
            }
        )
    return items


def _fetch_by_item_selector(soup, base, cfg):
    els = soup.select(cfg["item_selector"])
    items = []
    for el in els:
        href = el.get("href")
        if not href:
            continue
        items.append(
            {
                "title": el.get_text(strip=True),
                "url": urljoin(base, href),
                "snippet": "",
            }
        )
    return items
