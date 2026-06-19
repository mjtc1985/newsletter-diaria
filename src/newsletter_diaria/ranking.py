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
        backend_label = llm_config.backend
        if llm_config.backend == "local-cli":
            backend_label = f"local-cli:{llm_config.opencode.cli_command}"
        logger.info("Using %s backend for ranking and summaries", backend_label)
        return llm_rank_and_summarize(items, llm_config, sources_by_name)
    except Exception as exc:
        if ai_mode == "required":
            print(f"[warn] LLM backend failed ({exc}); using heuristic ranking so the newsletter still ships.", file=sys.stderr)
        else:
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


def llm_rank_and_summarize(items: list[Item], config: LLMConfig, sources_by_name: dict[str, Source]) -> NewsletterDraft:
    provider = build_provider(config)
    ranking = provider.rank(items)
    ranked_ids = parse_ranking_result(ranking, items, sources_by_name)
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


def parse_ranking_result(data: dict, items: list[Item], sources_by_name: dict[str, Source]) -> list[tuple[Item, int, int]]:
    by_uid = {item.uid: item for item in items}
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    ranked: list[tuple[Item, int, int]] = []
    seen_uids: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        uid = str(raw.get("uid", "")).strip()
        item = by_uid.get(uid)
        if not item:
            continue
        if uid in seen_uids:
            continue
        seen_uids.add(uid)
        rank = coerce_int(raw.get("rank"), default=len(ranked) + 1)
        if rank < 1:
            rank = len(ranked) + 1
        importance = coerce_int(raw.get("importance"), default=50)
        importance = max(1, min(100, importance))
        ranked.append((item, rank, importance))
    ranked.sort(key=lambda item: (item[1], -item[2]))

    ordered: list[tuple[Item, int, int]] = []
    for index, (item, _rank, importance) in enumerate(ranked, start=1):
        ordered.append((item, index, importance))

    missing = [item for item in items if item.uid not in seen_uids]
    if missing:
        missing_ranked = sorted(missing, key=lambda item: rank_item(item, sources_by_name), reverse=True)
        next_rank = len(ordered) + 1
        for item in missing_ranked:
            importance = max(1, min(100, int(rank_item(item, sources_by_name) * 4)))
            ordered.append((item, next_rank, importance))
            next_rank += 1

    return ordered


def coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
