from __future__ import annotations

from newsletter_diaria.models import Item, Source
from newsletter_diaria.parsers.base import SourceParser


class FeedParser(SourceParser):
    key = "feed"

    def parse(self, source: Source) -> list[Item]:
        from newsletter_diaria.ingest import fetch_xml, parse_feed

        return parse_feed(source.name, fetch_xml(source.url))
