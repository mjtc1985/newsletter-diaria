from __future__ import annotations

import argparse
import os
from pathlib import Path

from newsletter_diaria.models import AppConfig, OpenCodeConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the daily newsletter from the command line")
    parser.add_argument("--hours", type=int, default=24, help="Time window in hours to include")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of news items (0 = unlimited)")
    parser.add_argument("--output", type=Path, default=Path("output/daily.md"), help="Output Markdown path")
    parser.add_argument("--cache-file", type=Path, default=Path("output/latest.json"), help="Path to the latest generated newsletter JSON cache")
    parser.add_argument("--sources", type=Path, default=Path("sources.json"), help="Path to the sources JSON config")
    parser.add_argument("--ai-mode", choices=("auto", "required", "off"), default="auto", help="Use AI for ranking and summaries")
    parser.add_argument("--ai-candidates", type=int, default=30, help="Maximum number of candidates sent to AI")
    parser.add_argument("--opencode-model", default=os.getenv("NEWSLETTER_OPENCODE_MODEL"), help="provider/model value for opencode")
    parser.add_argument("--opencode-ranker-agent", default="newsletter-ranker", help="opencode agent used for ranking")
    parser.add_argument("--opencode-summarizer-agent", default="newsletter-summarizer", help="opencode agent used for summaries")
    parser.add_argument("--opencode-cwd", type=Path, default=Path.cwd(), help="Working directory for opencode")
    parser.add_argument("--send-email", action="store_true", help="Send the newsletter via Gmail SMTP")
    parser.add_argument("--email-to", default=os.getenv("NEWSLETTER_EMAIL_TO"), help="Email recipient")
    parser.add_argument("--email-from", default=os.getenv("NEWSLETTER_EMAIL_FROM") or os.getenv("NEWSLETTER_GMAIL_USER"), help="Gmail sender")
    parser.add_argument("--gmail-user", default=os.getenv("NEWSLETTER_GMAIL_USER"), help="Gmail username if different from sender")
    parser.add_argument("--gmail-password", default=os.getenv("NEWSLETTER_GMAIL_PASSWORD"), help="Gmail password or app password")
    parser.add_argument("--smtp-host", default=os.getenv("NEWSLETTER_SMTP_HOST", "smtp.gmail.com"), help="SMTP server")
    parser.add_argument("--smtp-port", type=int, default=int(os.getenv("NEWSLETTER_SMTP_PORT", "465")), help="SMTP port")
    parser.add_argument("--test-email", action="store_true", help="Test SMTP config without sending the newsletter")
    parser.add_argument("--send-latest", action="store_true", help="Send the latest cached newsletter without re-reading feeds")
    return parser


def parse_args(argv: list[str] | None = None) -> AppConfig:
    args = build_parser().parse_args(argv)
    return AppConfig(
        hours=args.hours,
        limit=args.limit,
        output=args.output,
        cache_file=args.cache_file,
        sources=args.sources,
        ai_mode=args.ai_mode,
        ai_candidates=args.ai_candidates,
        opencode=OpenCodeConfig(
            model=args.opencode_model,
            ranker_agent=args.opencode_ranker_agent,
            summarizer_agent=args.opencode_summarizer_agent,
            cwd=args.opencode_cwd,
        ),
        send_email=args.send_email,
        email_to=args.email_to,
        email_from=args.email_from,
        gmail_user=args.gmail_user,
        gmail_password=args.gmail_password,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        test_email=args.test_email,
        send_latest=args.send_latest,
    )
