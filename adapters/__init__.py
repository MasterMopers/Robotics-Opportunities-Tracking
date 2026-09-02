from . import rss, html, json_adapter, json_embedded, github  # noqa: F401

DISPATCH = {
    "rss": rss.fetch,
    "html": html.fetch,
    "json": json_adapter.fetch,
    "json_embedded": json_embedded.fetch,
    "github": github.fetch,
}


def fetch_source(source: dict) -> list:
    """Returns a list of dicts: {title, url, snippet}."""
    method = source["method"]
    if method not in DISPATCH:
        raise ValueError(f"Unknown adapter method: {method}")
    return DISPATCH[method](source)
