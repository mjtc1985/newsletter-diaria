from __future__ import annotations

import argparse
import os
from pathlib import Path

from newsletter_diaria.models import (
    DEFAULT_EDITORIAL_POLICY,
    AppConfig,
    EditorialPolicy,
    LLMConfig,
    OpenAICompatibleConfig,
    OpenCodeConfig,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the daily newsletter from the command line")
    parser.add_argument("--hours", type=int, default=72, help="Time window in hours to include")
    parser.add_argument("--limit", type=int, default=DEFAULT_EDITORIAL_POLICY.max_items, help="Maximum number of news items in the edition (0 = unlimited)")
    parser.add_argument("--output", type=Path, default=Path("output/daily.md"), help="Output Markdown path")
    parser.add_argument("--cache-file", type=Path, default=Path("output/latest.json"), help="Path to the latest generated newsletter JSON cache")
    parser.add_argument("--sources", type=Path, default=Path("sources.json"), help="Path to the sources JSON config")
    parser.add_argument("--ai-mode", choices=("auto", "required", "off"), default="auto", help="Use AI for ranking and summaries")
    parser.add_argument("--ai-candidates", type=int, default=30, help="Maximum number of candidates sent to AI")
    parser.add_argument("--fetch-bodies", action=argparse.BooleanOptionalAction, default=True, help="Download each candidate article and rank and summarize from its text instead of the feed blurb")
    parser.add_argument("--seen-file", type=Path, default=Path("output/seen.json"), help="Path to the store of already sent articles")
    parser.add_argument("--seen-retention-days", type=int, default=30, help="How long an article stays in the seen store")
    parser.add_argument("--min-importance", type=int, default=DEFAULT_EDITORIAL_POLICY.min_importance, help="Drop items ranked below this importance")
    parser.add_argument("--max-per-source", type=int, default=DEFAULT_EDITORIAL_POLICY.max_per_source, help="Maximum items per source in one edition (0 = unlimited)")
    parser.add_argument("--max-per-group", type=int, default=DEFAULT_EDITORIAL_POLICY.max_per_group, help="Maximum items per source group in one edition (0 = unlimited)")
    parser.add_argument("--max-per-group-override", action="append", default=None, metavar="GROUP=N", help="Per-group override of --max-per-group, repeatable (e.g. labs=5)")
    parser.add_argument("--reserved-topics", default=",".join(sorted(DEFAULT_EDITORIAL_POLICY.reserved_topics)), help="Comma-separated topics that get reserved slots")
    parser.add_argument("--reserved-slots", type=int, default=DEFAULT_EDITORIAL_POLICY.reserved_slots, help="Slots held for the reserved topics")
    parser.add_argument("--max-non-ai", type=int, default=DEFAULT_EDITORIAL_POLICY.max_non_ai, help="Maximum items not about AI in one edition (-1 = unlimited)")
    parser.add_argument("--require-why", action=argparse.BooleanOptionalAction, default=DEFAULT_EDITORIAL_POLICY.require_why, help="Drop items whose 'why it matters' came back empty")
    parser.add_argument("--relax-floor-if-empty", action=argparse.BooleanOptionalAction, default=DEFAULT_EDITORIAL_POLICY.relax_floor_if_empty, help="Relax the importance floor when it would leave the edition empty")
    parser.add_argument("--relaxed-max-items", type=int, default=DEFAULT_EDITORIAL_POLICY.relaxed_max_items, help="Maximum items when the importance floor is relaxed")
    parser.add_argument("--llm-backend", choices=("local-cli", "openai-compatible"), default=os.getenv("NEWSLETTER_LLM_BACKEND", "openai-compatible"), help="LLM backend used for ranking and summaries")
    parser.add_argument("--llm-cli-command", choices=("gemini", "opencode"), default=os.getenv("NEWSLETTER_LLM_CLI_COMMAND", "opencode"), help="CLI used by the local LLM backend")
    parser.add_argument("--local-cli-model", default=os.getenv("NEWSLETTER_LOCAL_CLI_MODEL"), help="provider/model value for the local CLI backend")
    parser.add_argument("--opencode-ranker-agent", default="newsletter-ranker", help="opencode agent used for ranking")
    parser.add_argument("--opencode-summarizer-agent", default="newsletter-summarizer", help="opencode agent used for summaries")
    parser.add_argument("--opencode-cwd", type=Path, default=Path.cwd(), help="Working directory for opencode")
    parser.add_argument("--llm-model", default=os.getenv("NEWSLETTER_LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gemini-flash-lite-latest", help="Model used by the OpenAI-compatible backend")
    parser.add_argument("--llm-base-url", default=os.getenv("NEWSLETTER_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL"), help="Base URL for the OpenAI-compatible backend")
    parser.add_argument("--llm-api-key", default=os.getenv("NEWSLETTER_LLM_API_KEY"), help="API key for the OpenAI-compatible backend")
    parser.add_argument("--llm-api-key-env", default=os.getenv("NEWSLETTER_LLM_API_KEY_ENV", "OPENAI_API_KEY"), help="Environment variable name to read the API key from when --llm-api-key is not set")
    parser.add_argument("--llm-json-mode", action=argparse.BooleanOptionalAction, default=os.getenv("NEWSLETTER_LLM_JSON_MODE", "1") not in {"0", "false", "False"}, help="Request JSON mode from the OpenAI-compatible backend when supported")
    parser.add_argument("--send-email", action="store_true", help="Send the newsletter via SMTP")
    parser.add_argument("--email-to", default=os.getenv("NEWSLETTER_EMAIL_TO"), help="Email recipient")
    parser.add_argument("--email-from", default=os.getenv("NEWSLETTER_EMAIL_FROM") or os.getenv("NEWSLETTER_SMTP_USERNAME"), help="Email sender")
    parser.add_argument("--smtp-username", default=os.getenv("NEWSLETTER_SMTP_USERNAME"), help="SMTP username")
    parser.add_argument("--smtp-password", default=os.getenv("NEWSLETTER_SMTP_PASSWORD"), help="SMTP password or app password")
    parser.add_argument("--smtp-host", default=os.getenv("NEWSLETTER_SMTP_HOST", "smtp.gmail.com"), help="SMTP server")
    parser.add_argument("--smtp-port", type=int, default=int(os.getenv("NEWSLETTER_SMTP_PORT", "465")), help="SMTP port")
    parser.add_argument("--smtp-ssl", action=argparse.BooleanOptionalAction, default=os.getenv("NEWSLETTER_SMTP_SSL", "1") not in {"0", "false", "False"}, help="Use implicit SSL for SMTP connections")
    parser.add_argument("--test-email", action="store_true", help="Test SMTP config without sending the newsletter")
    parser.add_argument("--send-latest", action="store_true", help="Send the latest cached newsletter without re-reading feeds")
    return parser


def parse_group_limits(raw: list[str] | None) -> tuple[tuple[str, int], ...]:
    """Sin --max-per-group-override se mantienen las excepciones por defecto."""
    if raw is None:
        return DEFAULT_EDITORIAL_POLICY.group_limits
    limits: dict[str, int] = {}
    for entry in raw:
        group, _, value = entry.partition("=")
        group = group.strip()
        if not group or not value.strip().lstrip("-").isdigit():
            raise SystemExit(f"Invalid --max-per-group-override value: {entry!r} (expected GROUP=N)")
        limits[group] = int(value)
    return tuple(sorted(limits.items()))


def parse_args(argv: list[str] | None = None) -> AppConfig:
    args = build_parser().parse_args(argv)
    backend = args.llm_backend
    if backend == "opencode":
        backend = "local-cli"
    local_cli_model = args.local_cli_model if args.llm_cli_command == "gemini" else None
    return AppConfig(
        hours=args.hours,
        limit=args.limit,
        output=args.output,
        cache_file=args.cache_file,
        sources=args.sources,
        ai_mode=args.ai_mode,
        ai_candidates=args.ai_candidates,
        llm=LLMConfig(
            backend=backend,
            opencode=OpenCodeConfig(
                cli_command=args.llm_cli_command,
                model=local_cli_model,
                ranker_agent=args.opencode_ranker_agent,
                summarizer_agent=args.opencode_summarizer_agent,
                cwd=args.opencode_cwd,
            ),
            openai_compatible=OpenAICompatibleConfig(
                base_url=args.llm_base_url,
                api_key=args.llm_api_key,
                api_key_env=args.llm_api_key_env,
                model=args.llm_model,
                json_mode=args.llm_json_mode,
            ),
        ),
        send_email=args.send_email,
        email_to=args.email_to,
        email_from=args.email_from,
        smtp_username=args.smtp_username,
        smtp_password=args.smtp_password,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        smtp_ssl=args.smtp_ssl,
        test_email=args.test_email,
        send_latest=args.send_latest,
        fetch_bodies=args.fetch_bodies,
        seen_file=args.seen_file,
        seen_retention_days=args.seen_retention_days,
        editorial=EditorialPolicy(
            min_importance=args.min_importance,
            max_items=args.limit,
            max_per_source=args.max_per_source,
            max_per_group=args.max_per_group,
            group_limits=parse_group_limits(args.max_per_group_override),
            reserved_topics=frozenset(
                topic.strip().lower() for topic in args.reserved_topics.split(",") if topic.strip()
            ),
            reserved_slots=args.reserved_slots,
            relax_floor_if_empty=args.relax_floor_if_empty,
            relaxed_max_items=args.relaxed_max_items,
            require_why=args.require_why,
            max_non_ai=args.max_non_ai,
        ),
    )
