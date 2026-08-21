from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from newsletter_diaria.emailing import send_newsletter_email
from newsletter_diaria.models import AppConfig, LLMConfig, NewsletterDraft, OpenAICompatibleConfig, OpenCodeConfig


class _DummySMTP:
    def __init__(self) -> None:
        self.logged_in = False
        self.sent_message = None

    def __enter__(self) -> "_DummySMTP":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        self.logged_in = True

    def send_message(self, message) -> None:
        self.sent_message = message
        wire_message = deepcopy(message)
        if wire_message["Bcc"] is not None:
            del wire_message["Bcc"]
        self.wire_message = wire_message.as_string()


class EmailingTest(unittest.TestCase):
    def test_sends_recipients_hidden_in_bcc(self) -> None:
        draft = NewsletterDraft(headline="Hola", items=[], trends=[])
        config = AppConfig(
            hours=24,
            limit=0,
            output=Path("output/daily.md"),
            cache_file=Path("output/latest.json"),
            sources=Path("sources.json"),
            ai_mode="off",
            ai_candidates=0,
            llm=LLMConfig(
                backend="local-cli",
                opencode=OpenCodeConfig(cli_command="opencode", model=None, ranker_agent="r", summarizer_agent="s", cwd=Path.cwd()),
                openai_compatible=OpenAICompatibleConfig(base_url=None, api_key=None, api_key_env="OPENAI_API_KEY", model=None, json_mode=True),
            ),
            send_email=True,
            email_to="a@example.com, b@example.com",
            email_from="sender@example.com",
            smtp_username="sender@example.com",
            smtp_password="secret",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_ssl=True,
            test_email=False,
            send_latest=False,
        )

        smtp = _DummySMTP()
        with patch("newsletter_diaria.emailing.build_smtp_client", return_value=smtp):
            send_newsletter_email(draft, config)

        self.assertIsNotNone(smtp.sent_message)
        self.assertEqual(smtp.sent_message["To"], "Undisclosed recipients:;")
        self.assertEqual(smtp.sent_message["Bcc"], "a@example.com, b@example.com")
        self.assertNotIn("a@example.com", smtp.wire_message)
        self.assertNotIn("b@example.com", smtp.wire_message)


if __name__ == "__main__":
    unittest.main()
