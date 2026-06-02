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
        logger.info("AI desactivada: usando ranking heurístico")
        return NewsletterDraft(headline="Resumen del día", items=heuristic_rank(items, sources_by_name), trends=[])

    try:
        resolve_opencode_bin()
    except RuntimeError:
        message = "No encuentro opencode en el sistema; usando ranking heurístico."
        if ai_mode == "required":
            raise RuntimeError(message)
        print(f"[warn] {message}", file=sys.stderr)
        return NewsletterDraft(headline="Resumen del día", items=heuristic_rank(items, sources_by_name), trends=[])

    try:
        logger.info("Usando OpenCode para ranking y resúmenes")
        return opencode_rank_and_summarize(items, opencode_config)
    except Exception as exc:
        if ai_mode == "required":
            raise
        print(f"[warn] opencode falló ({exc}); usando ranking heurístico.", file=sys.stderr)
        return NewsletterDraft(headline="Resumen del día", items=heuristic_rank(items, sources_by_name), trends=[])


def heuristic_rank(items: list[Item], sources_by_name: dict[str, Source]) -> list[RankedItem]:
    ranked_items = sorted(items, key=lambda item: rank_item(item, sources_by_name), reverse=True)
    return [
        RankedItem(
            item=item,
            rank=index,
            importance=min(100, int(rank_item(item, sources_by_name) * 4)),
            summary=textwrap.shorten(item.summary or item.title, width=240, placeholder="..."),
            why="Ranking heurístico por fecha y fuente.",
            takeaway="Revisión manual recomendada.",
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
        "Responde en español y SOLO con JSON válido. "
        "Ordena estas noticias por importancia real. "
        "Usa solo los campos dados. "
        "Devuelve exactamente {\"headline\":string,\"trends\":[string],\"items\":[{\"uid\":string,\"rank\":number,\"importance\":number}]}. "
        f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    logger.info("Prompt de ranking: %d caracteres", len(prompt))
    logger.info("Llamando a OpenCode para ranking de %d items", len(items))
    ranking = run_opencode_json(config=config, agent=config.ranker_agent, prompt=prompt)
    ranked_ids = parse_ranking_result(ranking, items)
    logger.info("OpenCode devolvió %d items rankeados", len(ranked_ids))
    summarized = summarize_ranked_items_batch(ranked_ids, config)
    summarized.sort(key=lambda item: (item.rank, -item.importance))
    logger.info("Resumenes completados")
    headline = str(ranking.get("headline", "Resumen del día")).strip() if isinstance(ranking, dict) else "Resumen del día"
    trends = [str(trend).strip() for trend in (ranking.get("trends", []) if isinstance(ranking, dict) else []) if str(trend).strip()]
    return NewsletterDraft(headline=headline or "Resumen del día", items=summarized, trends=trends)


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
        "Responde en español y SOLO con JSON válido. "
        "Resume TODOS los artículos de la lista en una sola respuesta. "
        "Haz que cada resumen sea un abstract breve y natural, de 2 a 4 frases, sin formato tipo ficha ni listas. "
        "Usa solo los campos dados. "
        "Devuelve exactamente {\"items\":[{\"uid\":string,\"summary\":string,\"why\":string,\"takeaway\":string}]}. "
        f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    logger.info("Llamando a OpenCode para resumir %d items en batch", len(ranked_ids))
    try:
        data = run_opencode_json(config=config, agent=config.summarizer_agent, prompt=prompt)
        summaries = parse_summary_batch_result(data, ranked_ids)
        if summaries:
            return summaries
    except Exception as exc:
        logger.warning("Batch de resúmenes falló: %s; usando fallback individual", exc)

    summarized: list[RankedItem] = []
    total = len(ranked_ids)
    for index, (item, rank, importance) in enumerate(ranked_ids, start=1):
        logger.info("[%d/%d] Resumiendo individualmente: %s", index, total, item.title)
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
        "Responde en español y SOLO con JSON válido. "
        "Resume el artículo sin inventar nada. "
        "Escribe un abstract breve y natural, de 2 a 4 frases, sin formato tipo ficha ni listas. "
        "Devuelve exactamente {\"summary\":string,\"why\":string,\"takeaway\":string}. "
        f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
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
        raise RuntimeError((completed.stderr or completed.stdout or "opencode falló").strip())
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
    raise RuntimeError("No encuentro el binario de opencode")


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
        raise ValueError("La IA no devolvió JSON válido")
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
