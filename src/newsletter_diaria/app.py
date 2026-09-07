from __future__ import annotations

import logging
import sys

from newsletter_diaria.cache import load_draft_cache, write_draft_cache
from newsletter_diaria.editorial import log_rejections, select_items
from newsletter_diaria.emailing import send_newsletter_email, test_email_config
from newsletter_diaria.ingest import cap_candidates, collect_items, dedupe, filter_recent
from newsletter_diaria.models import AppConfig, NewsletterDraft
from newsletter_diaria.ranking import build_newsletter
from newsletter_diaria.renderers import render_console, write_markdown
from newsletter_diaria.sources import load_sources, sources_by_name
from newsletter_diaria.state import filter_unseen, load_seen, record_seen

logger = logging.getLogger("newsletter_diaria")

def run(config: AppConfig) -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger.info("Starting daily newsletter run")

    if config.test_email:
        return _run_test_email(config)

    if config.send_latest:
        return _run_send_latest(config)

    logger.info("Loading sources from %s", config.sources)
    sources = load_sources(config.sources)
    source_index = sources_by_name(sources)

    logger.info("Reading feeds from %d sources", len(sources))
    all_items = collect_items(sources)
    logger.info("Fetched %d items", len(all_items))

    items = filter_recent(all_items, hours=config.hours)
    logger.info("Filtered down to %d recent items (last %d hours)", len(items), config.hours)

    items = dedupe(items)
    logger.info("%d items remain after deduplication", len(items))

    # Ventana amplia + memoria de lo ya enviado: asi una fuente que publica una
    # vez al mes puede competir varios dias sin que nada se repita.
    seen = load_seen(config.seen_file)
    before_seen = len(items)
    items = filter_unseen(items, seen)
    logger.info("%d items remain after dropping %d already sent", len(items), before_seen - len(items))

    if not items:
        logger.info("No unsent news items found in the last %d hours. Skipping newsletter generation.", config.hours)
        print("No new news items found.")
        return 0

    items = cap_candidates(items, config.ai_candidates)
    logger.info("Ranking candidates: %d", len(items))

    try:
        draft = build_newsletter(items, config.ai_mode, config.llm, source_index)
    except RuntimeError as exc:
        print(f"(x) {exc}", file=sys.stderr)
        return 1

    selection = select_items(
        draft.items,
        config.editorial,
        source_index,
        apply_importance_floor=not draft.heuristic_importance,
    )
    log_rejections(selection.rejected)
    logger.info("Editorial policy kept %d of %d item(s)", len(selection.items), len(draft.items))
    draft = NewsletterDraft(headline=draft.headline, items=selection.items, trends=draft.trends)

    if not draft.items:
        logger.info("Generated draft contains no items. Skipping email dispatch.")
        print("No recent news items found.")
        return 0

    logger.info("Rendering output and writing Markdown to %s", config.output)
    render_console(draft)
    write_markdown(draft, config.output)
    write_draft_cache(draft, config.cache_file)
    if config.send_email:
        try:
            send_newsletter_email(draft, config)
        except RuntimeError as exc:
            print(f"(x) {exc}", file=sys.stderr)
            return 1
        # Solo se anota lo entregado: si el envio falla, o esto es una prueba en
        # seco sin --send-email, los articulos siguen disponibles manana.
        record_seen(config.seen_file, [ranked.item for ranked in draft.items], seen, config.seen_retention_days)
    else:
        logger.info("Dry run without --send-email: the seen store at %s stays untouched", config.seen_file)

    print(f"\nSaved: {config.output}")
    return 0


def _run_test_email(config: AppConfig) -> int:
    try:
        test_email_config(config)
    except RuntimeError as exc:
        print(f"(x) {exc}", file=sys.stderr)
        return 1
    print("SMTP email configuration OK")
    return 0


def _run_send_latest(config: AppConfig) -> int:
    try:
        draft = load_draft_cache(config.cache_file)
        if not draft.items:
            raise RuntimeError(f"No cached newsletter found at {config.cache_file}")
        send_newsletter_email(draft, config)
    except RuntimeError as exc:
        print(f"(x) {exc}", file=sys.stderr)
        return 1
    print(f"\nSent from cache: {config.cache_file}")
    return 0
