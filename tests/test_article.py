from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from newsletter_diaria.article import (
    BODY_MIN_CHARS,
    extract_article_text,
    fetch_body,
    is_binary_link,
    scope_to_main_content,
)
from newsletter_diaria.models import Item

LONG = "Este parrafo tiene texto suficiente para pasar el minimo de bloque util. " * 4

PAGE = f"""
<html><head><style>.x{{color:red}}</style><script>var a=1;</script></head>
<body>
  <nav><a href="/">Inicio</a><a href="/b">Blog</a></nav>
  <header><p>Menu superior con enlaces</p></header>
  <article>
    <h1>Titulo del articulo</h1>
    <p>{LONG}</p>
    <p>Corto</p>
    <ul><li>{LONG}</li></ul>
    <script>trackear();</script>
    <p>{LONG}</p>
  </article>
  <aside><p>{LONG}</p></aside>
  <footer><p>{LONG}</p></footer>
</body></html>
"""


def make_item(link: str, summary: str = "resumen del feed") -> Item:
    return Item(uid="u1", source="Test", title="T", link=link,
                published_at=datetime.now(timezone.utc), summary=summary)


class ExtractArticleTextTest(unittest.TestCase):
    def test_extracts_content_and_drops_chrome(self) -> None:
        text = extract_article_text(PAGE)
        self.assertEqual(text.count(LONG.strip()), 3)  # p, li y p; no aside ni footer
        self.assertNotIn("Menu superior", text)
        self.assertNotIn("var a=1", text)
        self.assertNotIn("trackear", text)
        self.assertNotIn("Corto", text)  # bloque por debajo del minimo

    def test_scopes_to_article_when_present(self) -> None:
        scoped = scope_to_main_content(PAGE)
        self.assertNotIn("<footer>", scoped)
        self.assertIn("Titulo del articulo", scoped)

    def test_ignores_a_tiny_article_wrapper(self) -> None:
        page = "<html><body><article>corto</article><main><p>" + LONG + "</p></main></body></html>"
        self.assertIn(LONG.strip(), extract_article_text(page))

    def test_respects_the_char_cap(self) -> None:
        page = "<html><body><p>" + ("x" * 9000) + "</p></body></html>"
        self.assertEqual(len(extract_article_text(page, max_chars=500)), 500)

    def test_broken_html_returns_something_or_nothing_but_never_raises(self) -> None:
        self.assertIsInstance(extract_article_text("<p>sin cerrar <div><span>"), str)
        self.assertEqual(extract_article_text(""), "")


class FetchBodyTest(unittest.TestCase):
    def test_skips_binary_links(self) -> None:
        self.assertTrue(is_binary_link("https://x.dev/paper.pdf"))
        self.assertTrue(is_binary_link("https://x.dev/paper.PDF?v=2"))
        self.assertFalse(is_binary_link("https://x.dev/post"))
        item = make_item("https://x.dev/paper.pdf")
        with patch("newsletter_diaria.ingest.fetch_html") as fetch:
            self.assertEqual(fetch_body(item).body, "")
            fetch.assert_not_called()

    def test_stores_the_body_when_it_beats_the_feed_text(self) -> None:
        item = make_item("https://x.dev/post")
        with patch("newsletter_diaria.ingest.fetch_html", return_value=PAGE):
            self.assertGreater(len(fetch_body(item).body), BODY_MIN_CHARS)

    def test_keeps_the_feed_text_when_the_page_yields_little(self) -> None:
        item = make_item("https://x.dev/post", summary="x" * 900)
        with patch("newsletter_diaria.ingest.fetch_html", return_value="<html><body><p>corto</p></body></html>"):
            self.assertEqual(fetch_body(item).body, "")

    def test_a_failed_fetch_leaves_the_item_untouched(self) -> None:
        item = make_item("https://x.dev/post")
        with patch("newsletter_diaria.ingest.fetch_html", side_effect=RuntimeError("timeout")):
            self.assertEqual(fetch_body(item), item)


if __name__ == "__main__":
    unittest.main()
