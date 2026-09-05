import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown_list import parse_markdown_list

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nayafia_microgrants.md")


class TestMarkdownListAdapter(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE_PATH) as f:
            self.text = f.read()

    def test_39_items_from_committed_fixture(self):
        items = parse_markdown_list(self.text)
        self.assertEqual(len(items), 39)

    def test_every_item_has_title_and_url(self):
        items = parse_markdown_list(self.text)
        for item in items:
            self.assertTrue(item["title"])
            self.assertTrue(item["url"].startswith("http"))

    def test_vitadao_markdown_link_case(self):
        items = parse_markdown_list(self.text)
        vitadao = next(i for i in items if i["title"] == "VitaDAO Fellowship")
        self.assertEqual(vitadao["url"], "https://www.vitadao.com/fellowship")
        self.assertIn("longevity", vitadao["snippet"])

    def test_bare_url_case(self):
        items = parse_markdown_list(self.text)
        medici = next(i for i in items if i["title"] == "1517 Medici Project")
        self.assertEqual(medici["url"], "https://www.1517fund.com/medici-project")

    def test_block_with_no_italic_description_yields_empty_snippet(self):
        text = (
            "## Program With No Description\n"
            "https://example.com/grant\n"
            "\n"
            "## Program With Description\n"
            "https://example.com/grant2 <br>\n"
            "_A real description._\n"
        )
        items = parse_markdown_list(text)
        self.assertEqual(len(items), 2)
        no_desc = next(i for i in items if i["title"] == "Program With No Description")
        self.assertEqual(no_desc["snippet"], "")
        with_desc = next(i for i in items if i["title"] == "Program With Description")
        self.assertEqual(with_desc["snippet"], "A real description.")


if __name__ == "__main__":
    unittest.main()
