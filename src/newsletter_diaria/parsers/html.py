from __future__ import annotations

import logging

from newsletter_diaria.models import Item, Source
from newsletter_diaria.parsers.base import SourceParser

logger = logging.getLogger("newsletter_diaria")


class GenericHtmlParser(SourceParser):
    key = "html"

    def parse(self, source: Source) -> list[Item]:
        from newsletter_diaria.ingest import extract_candidate_links, fetch_html, parse_html_article

        listing = fetch_html(source.url)
        items: list[Item] = []
        for article_url in extract_candidate_links(source.url, listing)[: source.max_items]:
            try:
                items.append(parse_html_article(source.name, article_url))
            except Exception as exc:
                logger.warning("[%s] skipped article: %s", source.name, exc)
        return items


class AnthropicHtmlParser(SourceParser):
    key = "anthropic"

    def parse(self, source: Source) -> list[Item]:
        from newsletter_diaria.ingest import fetch_html, parse_anthropic_listing

        return parse_anthropic_listing(source, fetch_html(source.url))


class UberEngineeringHtmlParser(SourceParser):
    key = "uber_engineering"

    def parse(self, source: Source) -> list[Item]:
        from newsletter_diaria.ingest import fetch_html, parse_uber_listing

        return parse_uber_listing(source, fetch_html(source.url))
