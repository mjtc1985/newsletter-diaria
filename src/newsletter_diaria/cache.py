from __future__ import annotations

import json
from pathlib import Path

from newsletter_diaria.models import Item, NewsletterDraft, RankedItem
from newsletter_diaria.utils import parse_datetime


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
            uid=str(raw.get("uid", "")),
            source=str(raw.get("source", "")),
            title=str(raw.get("title", "")),
            link=str(raw.get("link", "")),
            published_at=parse_datetime(str(raw.get("published_at", ""))) if raw.get("published_at") else None,
            summary=str(raw.get("summary", "")),
        )
        ranked_items.append(
            RankedItem(
                item=item,
                rank=int(raw.get("rank", len(ranked_items) + 1)),
                importance=int(raw.get("importance", 50)),
                summary=str(raw.get("summary_ai", raw.get("summary", ""))),
                why=str(raw.get("why", "")),
                takeaway=str(raw.get("takeaway", "")),
            )
        )

    ranked_items.sort(key=lambda item: (item.rank, -item.importance))
    return NewsletterDraft(
        headline=str(data.get("headline", "Daily roundup")),
        trends=[str(value) for value in data.get("trends", []) if str(value).strip()],
        items=ranked_items,
    )
