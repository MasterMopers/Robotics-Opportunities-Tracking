"""Phase 4a: a generic adapter for a curated GitHub markdown list, verified
against nayafia/microgrants' README.md (39 real grant programs, 37 bare
URLs, 1 markdown link -- VitaDAO Fellowship, 38 italic descriptions).

The `github` adapter previously used to "watch" this list hit the commits
API and emitted commit messages, which monitor.py then force-routed to
`review` -- a commit message is not itself a submittable opportunity, so
that source yielded zero real grant listings from a file containing 39 of
them. This adapter reads the file's actual content instead.

Format (verified against the live file, not guessed):

    ## Program Name
    https://example.com/grant <br>
    _Italic one-line description._

- Split on `^## ` at line start. Heading text is the title.
- Within each block, the first URL -- either bare `https?://\\S+` or
  markdown `[text](url)` (the VitaDAO Fellowship entry uses the markdown
  form, so both must work).
- The `_..._` italic line is the snippet. A block with no italic line
  still yields an item, with an empty snippet.

Works for any curated GitHub list in this shape, not just nayafia's --
`source["url"]` names the raw file to fetch.
"""

import re

from ._http import get

_BARE_URL = re.compile(r"https?://\S+")
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)")
_ITALIC_LINE = re.compile(r"^_(.+)_\s*$", re.MULTILINE)


def _first_url(block: str):
    md = _MARKDOWN_LINK.search(block)
    if md:
        return md.group(1)
    bare = _BARE_URL.search(block)
    if bare:
        # Strip a trailing " <br>"/"</br>"-style HTML tag remnant or
        # markdown-link tail that isn't part of the bare URL itself.
        url = bare.group(0)
        url = re.sub(r"[<>].*$", "", url).strip()
        return url.rstrip(").,;")
    return None


def _snippet(block: str) -> str:
    m = _ITALIC_LINE.search(block)
    return m.group(1).strip() if m else ""


def parse_markdown_list(text: str) -> list:
    blocks = re.split(r"^## ", text, flags=re.MULTILINE)[1:]  # drop preamble before first heading
    items = []
    for block in blocks:
        lines = block.split("\n", 1)
        title = lines[0].strip()
        body = lines[1] if len(lines) > 1 else ""
        if not title:
            continue
        url = _first_url(body)
        if not url:
            continue
        items.append({"title": title, "url": url, "snippet": _snippet(body)})
    return items


def fetch(source: dict) -> list:
    resp = get(source["url"], headers=source.get("request_headers"))
    return parse_markdown_list(resp.text)
