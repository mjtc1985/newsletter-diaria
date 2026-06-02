from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from shutil import which as shutil_which

from newsletter_diaria.models import Item, NewsletterDraft, OpenCodeConfig, RankedItem, Source
from newsletter_diaria.sources import PRIORITY_WEIGHTS

logger = logging.getLogger("newsletter_diaria")


def build_newsletter(items: list[Item], ai_mode: str, opencode_config: OpenCodeConfig, sources_by_name: dict[str, Source]) -> NewsletterDraft:
    if not items:
        return NewsletterDraft(headline="", items=[], trends=[])

    if ai_mode == "off":
        logger.info("AI disabled: using heuristic ranking")
        return NewsletterDraft(headline="Daily roundup", items=heuristic_rank(items, sources_by_name), trends=[])

    try:
        resolve_opencode_bin()
    except RuntimeError:
        message = "Could not find opencode on the system; using heuristic ranking."
        if ai_mode == "required":
            raise RuntimeError(message)
        print(f"[warn] {message}", file=sys.stderr)
        return NewsletterDraft(headline="Daily roundup", items=heuristic_rank(items, sources_by_name), trends=[])

    try:
        logger.info("Using OpenCode for ranking and summaries")
        return opencode_rank_and_summarize(items, opencode_config)
    except Exception as exc:
        if ai_mode == "required":
            raise
        print(f"[warn] opencode failed ({exc}); using heuristic ranking.", file=sys.stderr)
        return NewsletterDraft(headline="Daily roundup", items=heuristic_rank(items, sources_by_name), trends=[])


def heuristic_rank(items: list[Item], sources_by_name: dict[str, Source]) -> list[RankedItem]:
    ranked_items = sorted(items, key=lambda item: rank_item(item, sources_by_name), reverse=True)
    return [
        RankedItem(
            item=item,
            rank=index,
            importance=min(100, int(rank_item(item, sources_by_name) * 4)),
            summary=textwrap.shorten(item.summary or item.title, width=240, placeholder="..."),
            why="Heuristic ranking based on recency and source.",
            takeaway="Manual review recommended.",
        )
        for index, item in enumerate(ranked_items, start=1)
    ]


def opencode_rank_and_summarize(items: list[Item], config: OpenCodeConfig) -> NewsletterDraft:
    payload = {
        "items": [
            {
                "uid": item.uid,
                "source": item.source,
                "title": item.title,
                "link": item.link,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "summary_excerpt": textwrap.shorten(item.summary or item.title, width=180, placeholder="..."),
            }
            for item in items
        ]
    }
    prompt = (
        "Respond in English and ONLY with valid JSON. "
        "Rank these news items by real-world importance. "
        "Use only the provided fields. "
        "Return exactly {\"headline\":string,\"trends\":[string],\"items\":[{\"uid\":string,\"rank\":number,\"importance\":number}]}. "
        f"Data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    logger.info("Ranking prompt length: %d characters", len(prompt))
    logger.info("Calling OpenCode to rank %d items", len(items))
    ranking = run_opencode_json(config=config, agent=config.ranker_agent, prompt=prompt)
    ranked_ids = parse_ranking_result(ranking, items)
    logger.info("OpenCode returned %d ranked items", len(ranked_ids))
    summarized = summarize_ranked_items_batch(ranked_ids, config)
    summarized.sort(key=lambda item: (item.rank, -item.importance))
    logger.info("Summaries completed")
    headline = str(ranking.get("headline", "Daily roundup")).strip() if isinstance(ranking, dict) else "Daily roundup"
    trends = [str(trend).strip() for trend in (ranking.get("trends", []) if isinstance(ranking, dict) else []) if str(trend).strip()]
    return NewsletterDraft(headline=headline or "Daily roundup", items=summarized, trends=trends)


def summarize_ranked_items_batch(ranked_ids: list[tuple[Item, int, int]], config: OpenCodeConfig) -> list[RankedItem]:
    if not ranked_ids:
        return []

    payload = {
        "items": [
            {
                "uid": item.uid,
                "source": item.source,
                "title": item.title,
                "link": item.link,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "summary": item.summary,
                "rank": rank,
                "importance": importance,
            }
            for item, rank, importance in ranked_ids
        ]
    }
    prompt = (
        "Respond in English and ONLY with valid JSON. "
        "Summarize ALL articles in the list in a single response. "
        "Make each summary a brief, natural abstract in 2 to 4 sentences, without bullet lists or card-like formatting. "
        "Use only the provided fields. "
        "Return exactly {\"items\":[{\"uid\":string,\"summary\":string,\"why\":string,\"takeaway\":string}]}. "
        f"Data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    logger.info("Calling OpenCode to summarize %d items in batch", len(ranked_ids))
    try:
        data = run_opencode_json(config=config, agent=config.summarizer_agent, prompt=prompt)
        summaries = parse_summary_batch_result(data, ranked_ids)
        if summaries:
            return summaries
    except Exception as exc:
        logger.warning("Summary batch failed: %s; using per-item fallback", exc)

    summarized: list[RankedItem] = []
    total = len(ranked_ids)
    for index, (item, rank, importance) in enumerate(ranked_ids, start=1):
        logger.info("[%d/%d] Summarizing individually: %s", index, total, item.title)
        summary_data = run_opencode_json(config=config, agent=config.summarizer_agent, prompt=make_summary_prompt(item))
        summarized.append(
            RankedItem(
                item=item,
                rank=rank,
                importance=importance,
                summary=str(summary_data.get("summary", item.summary)).strip(),
                why=str(summary_data.get("why", "")).strip(),
                takeaway=str(summary_data.get("takeaway", "")).strip(),
            )
        )
    return summarized


def make_summary_prompt(item: Item) -> str:
    payload = {
        "uid": item.uid,
        "source": item.source,
        "title": item.title,
        "link": item.link,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "summary": item.summary,
    }
    return (
        "Respond in English and ONLY with valid JSON. "
        "Summarize the article without inventing anything. "
        "Write a brief, natural abstract in 2 to 4 sentences, without bullet lists or card-like formatting. "
        "Return exactly {\"summary\":string,\"why\":string,\"takeaway\":string}. "
        f"Data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )


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
                summary=str(raw.get("summary", item.summary)).strip(),
                why=str(raw.get("why", "")).strip(),
                takeaway=str(raw.get("takeaway", "")).strip(),
            )
        )
    return result


def run_opencode_json(config: OpenCodeConfig, agent: str, prompt: str) -> dict:
    opencode_bin = resolve_opencode_bin()
    command = [opencode_bin, "run", "--agent", agent, "--format", "default", "--dir", str(config.cwd)]
    if config.model:
        command.extend(["--model", config.model])
    command.append(prompt)

    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "opencode failed").strip())
    return extract_json_object(completed.stdout)


def resolve_opencode_bin() -> str:
    if shutil_which("opencode"):
        return "opencode"
    for candidate in (
        Path.home() / ".opencode" / "bin" / "opencode",
        Path.home() / ".local" / "bin" / "opencode",
        Path.home() / ".bun" / "bin" / "opencode",
        Path("/usr/local/bin/opencode"),
        Path("/usr/bin/opencode"),
    ):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError("Could not find the opencode binary")


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


def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("The AI did not return valid JSON")
    return json.loads(cleaned[start : end + 1])


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
