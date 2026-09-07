from . import rss, html, json_adapter, json_embedded, github, markdown_list, bulk_xml, devpost_api  # noqa: F401

DISPATCH = {
    "rss": rss.fetch,
    "html": html.fetch,
    "json": json_adapter.fetch,
    "json_embedded": json_embedded.fetch,
    "github": github.fetch,
    "markdown_list": markdown_list.fetch,
    "bulk_xml": bulk_xml.fetch,
    "devpost_api": devpost_api.fetch,
}


def fetch_source(source: dict) -> list:
    """Returns a list of dicts: {title, url, snippet}."""
    method = source["method"]
    if method not in DISPATCH:
        raise ValueError(f"Unknown adapter method: {method}")
    return DISPATCH[method](source)
