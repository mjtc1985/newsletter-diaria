from __future__ import annotations

from newsletter_diaria.models import Source
from newsletter_diaria.parsers.base import SourceParser
from newsletter_diaria.parsers.feed import FeedParser
from newsletter_diaria.parsers.html import AnthropicHtmlParser, GenericHtmlParser, UberEngineeringHtmlParser


PARSERS: dict[str, SourceParser] = {
    FeedParser.key: FeedParser(),
    GenericHtmlParser.key: GenericHtmlParser(),
    AnthropicHtmlParser.key: AnthropicHtmlParser(),
    UberEngineeringHtmlParser.key: UberEngineeringHtmlParser(),
}


def resolve_parser(source: Source) -> SourceParser:
    if source.parser and source.parser in PARSERS:
        return PARSERS[source.parser]
    if source.kind == "html":
        return PARSERS[GenericHtmlParser.key]
    return PARSERS[FeedParser.key]
