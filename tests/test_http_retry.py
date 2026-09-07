import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from adapters._http import get


def _resp(status_code, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    if status_code >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error", response=r)
    else:
        r.raise_for_status.side_effect = None
    return r


class TestHttpRetry(unittest.TestCase):
    def test_succeeds_on_first_try_no_sleep(self):
        ok = _resp(200)
        with patch("adapters._http.requests.get", return_value=ok) as mock_get, \
             patch("adapters._http.time.sleep") as mock_sleep:
            resp = get("https://example.com")
        self.assertIs(resp, ok)
        self.assertEqual(mock_get.call_count, 1)
        mock_sleep.assert_not_called()

    def test_retries_on_429_then_succeeds(self):
        rate_limited = _resp(429)
        ok = _resp(200)
        with patch("adapters._http.requests.get", side_effect=[rate_limited, ok]) as mock_get, \
             patch("adapters._http.time.sleep") as mock_sleep:
            resp = get("https://example.com")
        self.assertIs(resp, ok)
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once()

    def test_honors_retry_after_header(self):
        rate_limited = _resp(429, headers={"Retry-After": "7"})
        ok = _resp(200)
        with patch("adapters._http.requests.get", side_effect=[rate_limited, ok]), \
             patch("adapters._http.time.sleep") as mock_sleep:
            get("https://example.com")
        mock_sleep.assert_called_once_with(7.0)

    def test_exhausts_three_attempts_then_raises(self):
        always_500 = [_resp(500), _resp(500), _resp(500)]
        with patch("adapters._http.requests.get", side_effect=always_500) as mock_get, \
             patch("adapters._http.time.sleep"):
            with self.assertRaises(requests.HTTPError):
                get("https://example.com")
        self.assertEqual(mock_get.call_count, 3)

    def test_network_exception_retried_then_raised(self):
        with patch(
            "adapters._http.requests.get",
            side_effect=requests.ConnectionError("boom"),
        ) as mock_get, patch("adapters._http.time.sleep"):
            with self.assertRaises(requests.ConnectionError):
                get("https://example.com")
        self.assertEqual(mock_get.call_count, 3)

    def test_non_retryable_4xx_raises_immediately(self):
        not_found = _resp(404)
        with patch("adapters._http.requests.get", return_value=not_found) as mock_get, \
             patch("adapters._http.time.sleep") as mock_sleep:
            with self.assertRaises(requests.HTTPError):
                get("https://example.com")
        self.assertEqual(mock_get.call_count, 1)
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
