from __future__ import annotations

import logging
import sys
import textwrap
from datetime import datetime, timezone

from newsletter_diaria.llm import build_provider
from newsletter_diaria.models import Item, LLMConfig, NewsletterDraft, RankedItem, Source
from newsletter_diaria.sources import PRIORITY_WEIGHTS

logger = logging.getLogger("newsletter_diaria")


def build_newsletter(items: list[Item], ai_mode: str, llm_config: LLMConfig, sources_by_name: dict[str, Source]) -> NewsletterDraft:
    if not items:
        return NewsletterDraft(headline="", items=[], trends=[])

    if ai_mode == "off":
        logger.info("AI disabled: using heuristic ranking")
        return NewsletterDraft(headline="Daily roundup", items=heuristic_rank(items, sources_by_name), trends=[])

    try:
        logger.info("Using %s backend for ranking and summaries", llm_config.backend)
        return llm_rank_and_summarize(items, llm_config)
    except Exception as exc:
        if ai_mode == "required":
            raise
        print(f"[warn] LLM backend failed ({exc}); using heuristic ranking.", file=sys.stderr)
        return NewsletterDraft(headline="Daily roundup", items=heuristic_rank(items, sources_by_name), trends=[])


def heuristic_rank(items: list[Item], sources_by_name: dict[str, Source]) -> list[RankedItem]:
    ranked_items = sorted(items, key=lambda item: rank_item(item, sources_by_name), reverse=True)
    return [
        RankedItem(
            item=item,
            rank=index,
            importance=min(100, int(rank_item(item, sources_by_name) * 4)),
            translated_title=None,
            summary=textwrap.shorten(item.summary or item.title, width=240, placeholder="..."),
            why="Heuristic ranking based on recency and source.",
            takeaway="Manual review recommended.",
        )
        for index, item in enumerate(ranked_items, start=1)
    ]


def llm_rank_and_summarize(items: list[Item], config: LLMConfig) -> NewsletterDraft:
    provider = build_provider(config)
    ranking = provider.rank(items)
    ranked_ids = parse_ranking_result(ranking, items)
    logger.info("LLM backend returned %d ranked items", len(ranked_ids))
    summarized = summarize_ranked_items_batch(ranked_ids, provider)
    summarized.sort(key=lambda item: (item.rank, -item.importance))
    logger.info("Summaries completed")
    headline = str(ranking.get("headline", "Resumen diario")).strip() if isinstance(ranking, dict) else "Resumen diario"
    trends = [str(trend).strip() for trend in (ranking.get("trends", []) if isinstance(ranking, dict) else []) if str(trend).strip()]
    return NewsletterDraft(headline=headline or "Resumen diario", items=summarized, trends=trends)


def summarize_ranked_items_batch(ranked_ids: list[tuple[Item, int, int]], provider) -> list[RankedItem]:
    if not ranked_ids:
        return []
    try:
        data = provider.summarize_batch(ranked_ids)
        summaries = parse_summary_batch_result(data, ranked_ids)
        if summaries:
            return summaries
    except Exception as exc:
        logger.warning("Summary batch failed: %s; using per-item fallback", exc)

    summarized: list[RankedItem] = []
    total = len(ranked_ids)
    for index, (item, rank, importance) in enumerate(ranked_ids, start=1):
        logger.info("[%d/%d] Summarizing individually: %s", index, total, item.title)
        summary_data = provider.summarize_one(item)
        summarized.append(
            RankedItem(
                item=item,
                rank=rank,
                importance=importance,
                translated_title=str(summary_data.get("title", "")).strip() or None,
                summary=str(summary_data.get("summary", item.summary)).strip(),
                why=str(summary_data.get("why", "")).strip(),
                takeaway=str(summary_data.get("takeaway", "")).strip(),
            )
        )
    return summarized


def parse_summary_batch_result(data: dict, ranked_ids: list[tuple[Item, int, int]]) -> list[RankedItem]:
    by_uid = {item.uid: (item, rank, importance) for item, rank, importance in ranked_ids}
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    result: list[RankedItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        uid = str(raw.get("uid", "")).strip()
        entry = by_uid.get(uid)
        if not entry:
            continue
        item, rank, importance = entry
        result.append(
            RankedItem(
                item=item,
                rank=rank,
                importance=importance,
                translated_title=str(raw.get("title", "")).strip() or None,
                summary=str(raw.get("summary", item.summary)).strip(),
                why=str(raw.get("why", "")).strip(),
                takeaway=str(raw.get("takeaway", "")).strip(),
            )
        )
    return result


def parse_ranking_result(data: dict, items: list[Item]) -> list[tuple[Item, int, int]]:
    by_uid = {item.uid: item for item in items}
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    ranked: list[tuple[Item, int, int]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        uid = str(raw.get("uid", "")).strip()
        item = by_uid.get(uid)
        if not item:
            continue
        ranked.append((item, int(raw.get("rank", len(ranked) + 1)), int(raw.get("importance", 50))))
    ranked.sort(key=lambda item: (item[1], -item[2]))
    return ranked


def rank_item(item: Item, sources_by_name: dict[str, Source]) -> float:
    score = 0.0
    if item.published_at:
        age_hours = (datetime.now(timezone.utc) - item.published_at).total_seconds() / 3600
        score += max(0.0, 24 - age_hours)

    source = sources_by_name.get(item.source)
    if source:
        score += PRIORITY_WEIGHTS.get(source.priority, 2.0)
        if source.topic in {"ai", "software", "research"}:
            score += 0.5
    else:
        score += 1.0

    score += min(len(item.summary) / 300, 2)
    return score
