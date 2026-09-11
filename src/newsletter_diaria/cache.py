from __future__ import annotations

import json
from pathlib import Path

from newsletter_diaria.models import Item, NewsletterDraft, RankedItem
from newsletter_diaria.utils import parse_datetime, text_value


def write_draft_cache(draft: NewsletterDraft, cache_file: Path) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "headline": draft.headline,
        "trends": draft.trends,
        "items": [
            {
                "uid": ranked.item.uid,
                "source": ranked.item.source,
                "title": ranked.item.title,
                "link": ranked.item.link,
                "published_at": ranked.item.published_at.isoformat() if ranked.item.published_at else None,
                "summary": ranked.item.summary,
                "rank": ranked.rank,
                "importance": ranked.importance,
                "translated_title": ranked.translated_title,
                "summary_ai": ranked.summary,
                "why": ranked.why,
                "takeaway": ranked.takeaway,
            }
            for ranked in draft.items
        ],
    }
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_draft_cache(cache_file: Path) -> NewsletterDraft:
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid cache file: {cache_file}")

    ranked_items: list[RankedItem] = []
    for raw in data.get("items", []):
        if not isinstance(raw, dict):
            continue
        item = Item(
            uid=text_value(raw.get("uid")),
            source=text_value(raw.get("source")),
            title=text_value(raw.get("title")),
            link=text_value(raw.get("link")),
            published_at=parse_datetime(text_value(raw.get("published_at"))) if raw.get("published_at") else None,
            summary=text_value(raw.get("summary")),
        )
        ranked_items.append(
            RankedItem(
                item=item,
                rank=int(raw.get("rank", len(ranked_items) + 1)),
                importance=int(raw.get("importance", 50)),
                translated_title=text_value(raw.get("translated_title")) or None,
                summary=text_value(raw.get("summary_ai"), text_value(raw.get("summary"))),
                why=text_value(raw.get("why")),
                takeaway=text_value(raw.get("takeaway")),
            )
        )

    ranked_items.sort(key=lambda item: (item.rank, -item.importance))
    return NewsletterDraft(
        headline=text_value(data.get("headline"), "Daily roundup"),
        trends=[trend for trend in (text_value(value) for value in data.get("trends", [])) if trend],
        items=ranked_items,
    )
