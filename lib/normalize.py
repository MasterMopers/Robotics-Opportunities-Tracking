"""URL normalization and stable item identity."""

import hashlib
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

STRIP_PARAM_PREFIXES = ("utm_",)
STRIP_PARAMS = {"ref", "fbclid", "gclid", "gclsrc", "mc_cid", "mc_eid", "igshid"}


def normalize_url(url: str) -> str:
    """Strip tracking params and trailing slashes so two URLs that only
    differ in tracking noise collapse to the same normalized form."""
    parts = urlsplit(url.strip())

    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in STRIP_PARAMS:
            continue
        if any(lower_key.startswith(p) for p in STRIP_PARAM_PREFIXES):
            continue
        kept.append((key, value))
    kept.sort()
    query = urlencode(kept)

    path = parts.path.rstrip("/") or "/"
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()

    return urlunsplit((scheme, netloc, path, query, ""))


def item_id(url: str) -> str:
    """Stable identity hash for an item, based on its normalized URL."""
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
