import base64
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("captain_guide_builder", ROOT / "docs/tools/build_docs.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class GuideTests(unittest.TestCase):
    def test_guides_are_synchronized(self):
        for source in sorted((ROOT / "docs").glob("*.md")):
            with self.subTest(source=source.name):
                self.assertEqual(source.with_suffix(".html").read_text(encoding="utf-8"), builder.render(source))

    def test_html_embeds_exact_markdown(self):
        for source in sorted((ROOT / "docs").glob("*.md")):
            with self.subTest(source=source.name):
                page = source.with_suffix(".html").read_text(encoding="utf-8")
                encoded = re.search(r'<script id="guide-source" type="text/plain">([^<]+)</script>', page).group(1)
                self.assertEqual(base64.b64decode(encoded).decode("utf-8"), source.read_text(encoding="utf-8"))

    def test_pages_have_no_network_assets(self):
        for source in sorted((ROOT / "docs").glob("*.html")):
            with self.subTest(source=source.name):
                page = source.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r'<(?:script|link|img)\b[^>]*(?:src|href)=["\']https?://', page))
