import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.normalize import item_id, normalize_url


class TestNormalizeUrl(unittest.TestCase):
    def test_strips_utm_params(self):
        a = "https://example.com/contest?utm_source=twitter&utm_campaign=x"
        b = "https://example.com/contest"
        self.assertEqual(normalize_url(a), normalize_url(b))

    def test_strips_ref_fbclid_gclid(self):
        a = "https://example.com/grant?ref=abc&fbclid=123&gclid=456"
        b = "https://example.com/grant"
        self.assertEqual(normalize_url(a), normalize_url(b))

    def test_strips_trailing_slash(self):
        a = "https://example.com/contest/"
        b = "https://example.com/contest"
        self.assertEqual(normalize_url(a), normalize_url(b))

    def test_case_insensitive_host(self):
        a = "https://Example.com/contest"
        b = "https://example.com/contest"
        self.assertEqual(normalize_url(a), normalize_url(b))

    def test_meaningful_query_params_preserved(self):
        a = "https://example.com/contest?id=42"
        b = "https://example.com/contest?id=43"
        self.assertNotEqual(normalize_url(a), normalize_url(b))

    def test_item_id_collapses_tracking_variants(self):
        a = "https://example.com/contest/?utm_source=x&ref=y"
        b = "https://example.com/contest?fbclid=z"
        self.assertEqual(item_id(a), item_id(b))

    def test_item_id_differs_for_different_paths(self):
        a = "https://example.com/contest-a"
        b = "https://example.com/contest-b"
        self.assertNotEqual(item_id(a), item_id(b))


if __name__ == "__main__":
    unittest.main()
