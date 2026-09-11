from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from html.parser import HTMLParser

from newsletter_diaria.models import Item
from newsletter_diaria.utils import clean_text

logger = logging.getLogger("newsletter_diaria")

# Tope de texto por articulo. Con 5 articulos por lote de resumen esto deja el
# prompt en unos 15.000 caracteres, holgado para el backend.
BODY_MAX_CHARS = 3000

# Minimo para considerar que la descarga aporta algo sobre el texto del feed.
BODY_MIN_CHARS = 200

# Bloques mas cortos que esto suelen ser menus, pies y enlaces sueltos.
MIN_BLOCK_CHARS = 40

BINARY_SUFFIXES = (".pdf", ".zip", ".tar", ".gz", ".mp3", ".mp4", ".png", ".jpg", ".jpeg", ".gif", ".webm")

MAX_BODY_WORKERS = 12


class ArticleTextParser(HTMLParser):
    """Extrae el texto de los bloques de contenido, ignorando navegacion y
    scripts. No usa dependencias externas: en la Raspberry solo hay stdlib."""

    SKIP = {"script", "style", "noscript", "svg", "template", "nav", "header", "footer", "aside", "form", "button", "select"}
    KEEP = {"p", "li", "h1", "h2", "h3", "h4", "blockquote", "pre", "dd", "figcaption"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.keep_stack: list[str] = []
        self.blocks: list[str] = []
        self.current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "br":
            self.current.append(" ")
        elif tag in self.KEEP:
            self.keep_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in self.KEEP and self.keep_stack:
            self.keep_stack.pop()
            self.flush()

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not self.keep_stack:
            return
        self.current.append(data)

    def flush(self) -> None:
        text = clean_text("".join(self.current))
        self.current = []
        if text:
            self.blocks.append(text)


def scope_to_main_content(html_text: str) -> str:
    """Si la pagina marca <article> o <main>, nos quedamos con esa region."""
    for tag in ("article", "main"):
        match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html_text, re.I | re.S)
        if match and len(match.group(1)) > 500:
            return match.group(1)
    return html_text


def extract_article_text(html_text: str, max_chars: int = BODY_MAX_CHARS) -> str:
    parser = ArticleTextParser()
    try:
        parser.feed(scope_to_main_content(html_text))
        parser.close()
    except Exception as exc:  # pragma: no cover - HTML roto
        logger.debug("Could not parse the article HTML: %s", exc)
        return ""
    parser.flush()
    blocks = [block for block in parser.blocks if len(block) >= MIN_BLOCK_CHARS]
    return "\n".join(blocks)[:max_chars].strip()


def is_binary_link(link: str) -> bool:
    return link.lower().split("?", 1)[0].endswith(BINARY_SUFFIXES)


def fetch_body(item: Item) -> Item:
    """Descarga el articulo y guarda su texto en 'body'. Si no aporta mas que el
    texto del feed, se deja el item como estaba."""
    from newsletter_diaria.ingest import fetch_html

    if not item.link or is_binary_link(item.link):
        return item
    try:
        text = extract_article_text(fetch_html(item.link))
    except Exception as exc:
        logger.info("Body fetch failed for %s (%s); using the feed text", item.link[:70], exc)
        return item
    if len(text) < max(BODY_MIN_CHARS, len(item.summary)):
        return item
    return replace(item, body=text)


def enrich_items(items: list[Item]) -> list[Item]:
    """El ranking y los resumenes se hacian con el blurb del RSS. Para las
    agregadoras eso son 8 o 145 caracteres de texto administrativo, y para los
    blogs de vendor es su propia copia de marketing."""
    if not items:
        return items
    with ThreadPoolExecutor(max_workers=min(MAX_BODY_WORKERS, len(items))) as executor:
        enriched = list(executor.map(fetch_body, items))
    with_body = sum(1 for item in enriched if item.body)
    logger.info("Article body fetched for %d of %d candidates", with_body, len(enriched))
    return enriched
