import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.extract import extract_document


class TestExtractDocument(unittest.TestCase):
    def test_script_and_style_stripped_from_text(self):
        html = (
            "<html><head><style>.a{color:red}</style></head>"
            "<body><script>var x = 1;</script>"
            "<nav>Home | About</nav>"
            "<header>Site Header</header>"
            "<p>Real content here.</p>"
            "<footer>Copyright 2026</footer>"
            "</body></html>"
        )
        doc = extract_document(html)
        self.assertIn("Real content here.", doc.text)
        for noise in ("color:red", "var x = 1", "Home | About", "Site Header", "Copyright 2026"):
            self.assertNotIn(noise, doc.text)

    def test_whitespace_collapsed(self):
        html = "<html><body><p>Line one</p>\n\n\n<p>   Line   two   </p></body></html>"
        doc = extract_document(html)
        self.assertNotIn("  ", doc.text)  # no double-spaces
        self.assertIn("Line one", doc.text)
        self.assertIn("Line two", doc.text)

    def test_jsonld_recovered_from_page_with_empty_visible_text(self):
        # JS-shell page: nothing in <body> but a JSON-LD payload -- exactly
        # the shape of a JS-rendered Devpost/Hackster/MLH detail page shell.
        html = (
            "<html><head>"
            '<script type="application/ld+json">'
            '{"@type": "Event", "name": "Empty Shell Hackathon", "applicationDeadline": "2026-11-01"}'
            "</script>"
            "</head><body></body></html>"
        )
        doc = extract_document(html)
        self.assertEqual(doc.text, "")
        self.assertEqual(len(doc.structured), 1)
        self.assertEqual(doc.structured[0]["applicationDeadline"], "2026-11-01")

    def test_malformed_jsonld_skipped_without_raising(self):
        html = (
            "<html><head>"
            '<script type="application/ld+json">{ this is not json </script>'
            "</head><body><p>Some real text.</p></body></html>"
        )
        try:
            doc = extract_document(html)
        except Exception as e:  # pragma: no cover - failure path
            self.fail(f"extract_document raised on malformed JSON-LD: {e}")
        self.assertEqual(doc.structured, [])
        self.assertIn("Some real text.", doc.text)

    def test_multiple_jsonld_blocks_all_collected(self):
        html = (
            "<html><head>"
            '<script type="application/ld+json">{"@type": "Organization", "name": "Foo"}</script>'
            '<script type="application/ld+json">{"@type": "Event", "name": "Bar"}</script>'
            "</head><body></body></html>"
        )
        doc = extract_document(html)
        types = {d.get("@type") for d in doc.structured}
        self.assertEqual(types, {"Organization", "Event"})

    def test_jsonld_list_payload_flattened(self):
        html = (
            "<html><head>"
            '<script type="application/ld+json">[{"@type": "Event", "name": "A"}, '
            '{"@type": "Event", "name": "B"}]</script>'
            "</head><body></body></html>"
        )
        doc = extract_document(html)
        self.assertEqual(len(doc.structured), 2)

    def test_next_data_recovered(self):
        html = (
            "<html><body>"
            '<script id="__NEXT_DATA__" type="application/json">'
            '{"props": {"pageProps": {"deadline": "2026-12-01"}}}'
            "</script>"
            "</body></html>"
        )
        doc = extract_document(html)
        self.assertEqual(len(doc.structured), 1)
        self.assertEqual(doc.structured[0]["props"]["pageProps"]["deadline"], "2026-12-01")

    def test_nuxt_data_recovered(self):
        html = (
            "<html><body>"
            '<script>window.__NUXT__={"data": {"title": "Some Contest"}};</script>'
            "</body></html>"
        )
        doc = extract_document(html)
        self.assertEqual(len(doc.structured), 1)
        self.assertEqual(doc.structured[0]["data"]["title"], "Some Contest")

    def test_empty_html_returns_empty_document(self):
        doc = extract_document("")
        self.assertEqual(doc.text, "")
        self.assertEqual(doc.structured, [])


if __name__ == "__main__":
    unittest.main()
