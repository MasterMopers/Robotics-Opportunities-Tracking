import random
import time

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "robotics-opportunities-tracker/1.0 (+https://github.com/MasterMopers/Robotics-Opportunities-Tracking)"
)

# Phase 5: a single 429/5xx used to make a source ERROR for a full week
# (no retry anywhere in this module). Three attempts total (i.e. up to two
# retries), exponential backoff with jitter, honoring a server's own
# Retry-After header when present. This only ever changes *how many times*
# a request is attempted before failing -- run_source()'s status logic is
# unchanged: ERROR still means network/5xx failure after every attempt is
# exhausted, BROKEN still means a 200 with fewer items than min_expected
# (selector rot), and this module has no opinion on that distinction.
MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 20.0

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _parse_retry_after(resp) -> float:
    """Retry-After is either a number of seconds or an HTTP-date; only the
    seconds form is common in practice and worth the complexity of
    supporting here. Anything else falls back to the exponential backoff."""
    header = resp.headers.get("Retry-After") if resp is not None else None
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        return None


def _backoff_delay(attempt: int, resp=None) -> float:
    retry_after = _parse_retry_after(resp)
    if retry_after is not None:
        return retry_after
    base = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    jitter = random.uniform(0, base * 0.25)
    return base + jitter


def get(url: str, headers: dict = None, timeout: int = 20) -> requests.Response:
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)

    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=merged, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            if attempt < MAX_ATTEMPTS:
                time.sleep(_backoff_delay(attempt))
                continue
            raise

        if resp.status_code in _RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
            time.sleep(_backoff_delay(attempt, resp))
            continue

        resp.raise_for_status()
        return resp

    # Unreachable in practice (the loop above always either returns or
    # raises), but keeps the function's control flow explicit rather than
    # implicitly returning None.
    if last_exc:
        raise last_exc
    raise RuntimeError(f"adapters._http.get: exhausted {MAX_ATTEMPTS} attempts for {url}")
