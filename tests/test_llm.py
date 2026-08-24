from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from newsletter_diaria.llm import OpenAICompatibleProvider, OpenCodeProvider
from newsletter_diaria.models import OpenAICompatibleConfig, OpenCodeConfig


class OpenCodeCliTest(unittest.TestCase):
    def test_opencode_backend_runs_agent(self) -> None:
        provider = OpenCodeProvider(
            OpenCodeConfig(
                cli_command="opencode",
                model=None,
                ranker_agent="newsletter-ranker",
                summarizer_agent="newsletter-summarizer",
                cwd=Path.cwd(),
            )
        )

        completed = subprocess.CompletedProcess(args=["opencode"], returncode=0, stdout='{"ok":true}', stderr="")

        with patch("newsletter_diaria.llm.resolve_cli_bin", return_value="opencode"), patch(
            "newsletter_diaria.llm.subprocess.run", return_value=completed
        ) as run:
            result = provider._run_json(agent="newsletter-ranker", prompt='{"ok":1}')

        self.assertEqual(result, {"ok": True})
        run.assert_called_once()


class OpenAICompatibleTest(unittest.TestCase):
    def test_fallback_on_timeout_error(self) -> None:
        config = OpenAICompatibleConfig(
            base_url="https://example.com/v1",
            api_key="secret",
            api_key_env="OPENAI_API_KEY",
            model="gemini-flash-latest",
            json_mode=True,
        )
        provider = OpenAICompatibleProvider(config)

        def fake_post_chat(prompt: str, model: str, allow_backoff: bool):
            if model == "gemini-flash-latest":
                raise TimeoutError("The read operation timed out")
            return {
                "choices": [
                    {"message": {"content": '{"headline": "Fallback OK", "items": [], "trends": []}'}}
                ]
            }

        with patch.object(provider, "_post_chat", side_effect=fake_post_chat):
            result = provider._chat_json("prompt")

        self.assertEqual(result.get("headline"), "Fallback OK")


if __name__ == "__main__":
    unittest.main()
