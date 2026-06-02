from __future__ import annotations

import argparse
import os
from pathlib import Path

from newsletter_diaria.models import AppConfig, OpenCodeConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Newsletter diaria por consola")
    parser.add_argument("--hours", type=int, default=24, help="Ventana de horas a incluir")
    parser.add_argument("--limit", type=int, default=0, help="Numero maximo de noticias (0 = sin limite)")
    parser.add_argument("--output", type=Path, default=Path("output/daily.md"), help="Ruta Markdown de salida")
    parser.add_argument("--cache-file", type=Path, default=Path("output/latest.json"), help="Ruta JSON del último boletín generado")
    parser.add_argument("--sources", type=Path, default=Path("sources.json"), help="Ruta a la config de fuentes JSON")
    parser.add_argument("--ai-mode", choices=("auto", "required", "off"), default="auto", help="Uso de IA para ranking y resumen")
    parser.add_argument("--ai-candidates", type=int, default=30, help="Maximo de candidatos enviados a la IA")
    parser.add_argument("--opencode-model", default=os.getenv("NEWSLETTER_OPENCODE_MODEL"), help="Modelo provider/model para opencode")
    parser.add_argument("--opencode-ranker-agent", default="newsletter-ranker", help="Agente opencode para ranking")
    parser.add_argument("--opencode-summarizer-agent", default="newsletter-summarizer", help="Agente opencode para resúmenes")
    parser.add_argument("--opencode-cwd", type=Path, default=Path.cwd(), help="Directorio de trabajo para opencode")
    parser.add_argument("--send-email", action="store_true", help="Enviar la newsletter por Gmail SMTP")
    parser.add_argument("--email-to", default=os.getenv("NEWSLETTER_EMAIL_TO"), help="Destinatario del email")
    parser.add_argument("--email-from", default=os.getenv("NEWSLETTER_EMAIL_FROM") or os.getenv("NEWSLETTER_GMAIL_USER"), help="Remitente Gmail")
    parser.add_argument("--gmail-user", default=os.getenv("NEWSLETTER_GMAIL_USER"), help="Usuario Gmail (si difiere del remitente)")
    parser.add_argument("--gmail-password", default=os.getenv("NEWSLETTER_GMAIL_PASSWORD"), help="Contraseña o app password Gmail")
    parser.add_argument("--smtp-host", default=os.getenv("NEWSLETTER_SMTP_HOST", "smtp.gmail.com"), help="Servidor SMTP")
    parser.add_argument("--smtp-port", type=int, default=int(os.getenv("NEWSLETTER_SMTP_PORT", "465")), help="Puerto SMTP")
    parser.add_argument("--test-email", action="store_true", help="Probar config SMTP sin enviar newsletter")
    parser.add_argument("--send-latest", action="store_true", help="Enviar el último boletín cacheado sin volver a leer feeds")
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
