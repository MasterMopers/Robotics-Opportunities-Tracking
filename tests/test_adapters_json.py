import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.json_adapter import fetch

PAGE_1 = {
    "results": [{"title": "Robot Challenge", "url": "https://example.com/1", "promo": "a robot thing"}],
    "next": "https://example.com/api?page=2",
}
PAGE_2 = {
    "results": [{"title": "Cooking Contest", "url": "https://example.com/2", "promo": "a recipe thing"}],
    "next": "https://example.com/api?page=3",
}
PAGE_3 = {
    "results": [{"title": "Another Robot Thing", "url": "https://example.com/3", "promo": "robotics again"}],
    "next": None,
}

PAGES_BY_URL = {
    "https://example.com/api?page=1": PAGE_1,
    "https://example.com/api?page=2": PAGE_2,
    "https://example.com/api?page=3": PAGE_3,
}


class FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class TestJsonAdapterPagination(unittest.TestCase):
    def test_follows_next_field_until_null(self):
        def fake_get(url, headers=None):
            return FakeResp(PAGES_BY_URL[url])

        source = {
            "url": "https://example.com/api?page=1",
            "json_items_path": "results",
            "paginate_next_field": "next",
            "filter_keywords_any": ["robot"],
        }
        with patch("adapters.json_adapter.get", side_effect=fake_get):
            items = fetch(source)

        titles = {i["title"] for i in items}
        self.assertEqual(titles, {"Robot Challenge", "Another Robot Thing"})
        self.assertNotIn("Cooking Contest", titles)

    def test_max_pages_bounds_pagination(self):
        calls = []

        def fake_get(url, headers=None):
            calls.append(url)
            return FakeResp(PAGES_BY_URL[url])

        source = {
            "url": "https://example.com/api?page=1",
            "json_items_path": "results",
            "paginate_next_field": "next",
            "max_pages": 1,
        }
        with patch("adapters.json_adapter.get", side_effect=fake_get):
            items = fetch(source)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(items), 1)

    def test_no_pagination_field_fetches_single_page(self):
        def fake_get(url, headers=None):
            return FakeResp(PAGE_1)

        source = {"url": "https://example.com/api?page=1", "json_items_path": "results"}
        with patch("adapters.json_adapter.get", side_effect=fake_get):
            items = fetch(source)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Robot Challenge")


if __name__ == "__main__":
    unittest.main()
