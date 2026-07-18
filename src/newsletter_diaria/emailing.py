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
        raise RuntimeError("Missing --email-to or NEWSLETTER_EMAIL_TO")
    smtp_password = config.smtp_password
    if not smtp_password:
        raise RuntimeError("Missing --smtp-password or NEWSLETTER_SMTP_PASSWORD")

    smtp_username = config.smtp_username
    sender = config.email_from or smtp_username
    if not sender:
        raise RuntimeError("Missing --email-from or NEWSLETTER_EMAIL_FROM")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = "Undisclosed recipients:;"
    message["Bcc"] = ", ".join(recipients)
    message["Subject"] = draft.headline or "Daily roundup"
    message.set_content(render_email_text(draft))
    message.add_alternative(render_email_html(draft), subtype="html")

    logger.info("Sending email to %d recipient(s) via %s:%s", len(recipients), config.smtp_host, config.smtp_port)
    with build_smtp_client(config) as client:
        if smtp_username and smtp_password:
            client.login(smtp_username, smtp_password)
        client.send_message(message)
    logger.info("Email sent")


def test_email_config(config: AppConfig) -> None:
    if not parse_recipients(config.email_to):
        raise RuntimeError("Missing --email-to or NEWSLETTER_EMAIL_TO")
    smtp_password = config.smtp_password
    if not smtp_password:
        raise RuntimeError("Missing --smtp-password or NEWSLETTER_SMTP_PASSWORD")

    smtp_username = config.smtp_username
    sender = config.email_from or smtp_username
    if not sender:
        raise RuntimeError("Missing --email-from or NEWSLETTER_EMAIL_FROM")

    logger.info("Testing SMTP against %s:%s", config.smtp_host, config.smtp_port)
    with build_smtp_client(config) as client:
        if smtp_username and smtp_password:
            client.login(smtp_username, smtp_password)


def build_smtp_client(config: AppConfig):
    if config.smtp_ssl:
        return smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=30)

    client = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30)
    client.ehlo()
    client.starttls()
    client.ehlo()
    return client


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
