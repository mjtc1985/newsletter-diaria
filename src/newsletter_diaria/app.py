from __future__ import annotations

import logging
import sys

from newsletter_diaria.cache import load_draft_cache, write_draft_cache
from newsletter_diaria.emailing import send_newsletter_email, test_email_config
from newsletter_diaria.ingest import cap_candidates, collect_items, dedupe, filter_recent
from newsletter_diaria.models import AppConfig, NewsletterDraft
from newsletter_diaria.ranking import build_newsletter
from newsletter_diaria.renderers import render_console, write_markdown
from newsletter_diaria.sources import load_sources, sources_by_name

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
    items = collect_items(sources)
    logger.info("Fetched %d items", len(items))
    items = filter_recent(items, hours=config.hours)
    logger.info("Filtered down to %d recent items (last %d hours)", len(items), config.hours)
    items = dedupe(items)
    logger.info("%d items remain after deduplication", len(items))
    items = cap_candidates(items, config.ai_candidates)
    logger.info("Ranking candidates: %d", len(items))

    try:
        draft = build_newsletter(items, config.ai_mode, config.opencode, source_index)
    except RuntimeError as exc:
        print(f"(x) {exc}", file=sys.stderr)
        return 1

    if config.limit > 0:
        draft = NewsletterDraft(headline=draft.headline, items=draft.items[: config.limit], trends=draft.trends)

    if not draft.items:
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
