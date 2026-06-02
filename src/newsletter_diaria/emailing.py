from __future__ import annotations

import logging
import re
import smtplib
from email.message import EmailMessage

from newsletter_diaria.models import AppConfig, NewsletterDraft
from newsletter_diaria.renderers import render_email_html, render_email_text

logger = logging.getLogger("newsletter_diaria")


def send_newsletter_email(draft: NewsletterDraft, config: AppConfig) -> None:
    recipients = parse_recipients(config.email_to)
    if not recipients:
        raise RuntimeError("Falta --email-to o NEWSLETTER_EMAIL_TO")
    if not config.gmail_password:
        raise RuntimeError("Falta --gmail-password o NEWSLETTER_GMAIL_PASSWORD")

    sender = config.email_from or config.gmail_user
    if not sender:
        raise RuntimeError("Falta --email-from o NEWSLETTER_EMAIL_FROM")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = draft.headline or "Resumen del día"
    message.set_content(render_email_text(draft))
    message.add_alternative(render_email_html(draft), subtype="html")

    logger.info("Enviando email a %s via %s:%s", ", ".join(recipients), config.smtp_host, config.smtp_port)
    with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=30) as client:
        client.login(config.gmail_user or sender, config.gmail_password)
        client.send_message(message, to_addrs=recipients)
    logger.info("Email enviado")


def test_email_config(config: AppConfig) -> None:
    if not parse_recipients(config.email_to):
        raise RuntimeError("Falta --email-to o NEWSLETTER_EMAIL_TO")
    if not config.gmail_password:
        raise RuntimeError("Falta --gmail-password o NEWSLETTER_GMAIL_PASSWORD")

    sender = config.email_from or config.gmail_user
    if not sender:
        raise RuntimeError("Falta --email-from o NEWSLETTER_EMAIL_FROM")

    logger.info("Probando SMTP contra %s:%s", config.smtp_host, config.smtp_port)
    with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=30) as client:
        client.login(config.gmail_user or sender, config.gmail_password)


def parse_recipients(value: str | None) -> list[str]:
    if not value:
        return []
    recipients: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[;,\n]+", value):
        addr = part.strip()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        recipients.append(addr)
    return recipients
