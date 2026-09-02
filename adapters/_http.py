import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "robotics-opportunities-tracker/1.0 (+https://github.com/MasterMopers/Robotics-Opportunities-Tracking)"
)


def get(url: str, headers: dict = None, timeout: int = 20) -> requests.Response:
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    resp = requests.get(url, headers=merged, timeout=timeout)
    resp.raise_for_status()
    return resp
