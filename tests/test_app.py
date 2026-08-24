from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from newsletter_diaria.app import run
from newsletter_diaria.models import AppConfig, LLMConfig, OpenAICompatibleConfig, OpenCodeConfig


class AppRunTest(unittest.TestCase):
    def test_run_skips_when_no_recent_items(self) -> None:
        config = AppConfig(
            hours=24,
            limit=0,
            output=Path("output/daily.md"),
            cache_file=Path("output/latest.json"),
            sources=Path("sources.json"),
            ai_mode="auto",
            ai_candidates=30,
            llm=LLMConfig(
                backend="openai-compatible",
                opencode=OpenCodeConfig(cli_command="opencode", model=None, ranker_agent="r", summarizer_agent="s", cwd=Path.cwd()),
                openai_compatible=OpenAICompatibleConfig(base_url="https://example.com", api_key="k", api_key_env="K", model="gemini-flash-lite-latest", json_mode=True),
            ),
            send_email=True,
            email_to="recipient@example.com",
            email_from="sender@example.com",
            smtp_username="sender@example.com",
            smtp_password="password",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_ssl=True,
            test_email=False,
            send_latest=False,
        )

        with patch("newsletter_diaria.app.load_sources", return_value=[]), \
             patch("newsletter_diaria.app.collect_items", return_value=[]), \
             patch("newsletter_diaria.app.send_newsletter_email") as mock_send:
            ret = run(config)

        self.assertEqual(ret, 0)
        mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
