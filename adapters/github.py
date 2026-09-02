import os

from ._http import get


def fetch(source: dict) -> list:
    url = (
        f"https://api.github.com/repos/{source['repo']}/commits"
        f"?path={source['watch_path']}&per_page=20"
    )
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = get(url, headers=headers)
    commits = resp.json()

    items = []
    for c in commits:
        message = (c.get("commit", {}).get("message") or "").strip()
        if not message:
            continue
        summary = message.splitlines()[0]
        html_url = c.get("html_url", "")
        if not html_url:
            continue
        items.append(
            {
                "title": f"{source['watch_path']} updated: {summary}",
                "url": html_url,
                "snippet": message,
            }
        )
    return items
