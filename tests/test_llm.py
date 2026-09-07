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

    def test_fallback_on_json_parse_error(self) -> None:
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
                return {
                    "choices": [
                        {"message": {"content": "INVALID NOT JSON AT ALL {"}}
                    ]
                }
            return {
                "choices": [
                    {"message": {"content": '{"headline": "Reserva JSON OK", "items": [], "trends": []}'}}
                ]
            }

        with patch.object(provider, "_post_chat", side_effect=fake_post_chat):
            result = provider._chat_json("prompt")

        self.assertEqual(result.get("headline"), "Reserva JSON OK")

    def test_extract_json_object_repairs_malformed_json(self) -> None:
        from newsletter_diaria.llm import extract_json_object

        malformed = """
        ```json
        {
          "headline": "Noticias de hoy",
          ": "trends": [
            "IA",
            "DevOps",
          ],
          "items": [
            {
              "uid": "1",
              "rank": 1,
              "importance": 90,
            }
          ]
        }
        ```
        """
        parsed = extract_json_object(malformed)
        self.assertEqual(parsed.get("headline"), "Noticias de hoy")
        self.assertEqual(parsed.get("trends"), ["IA", "DevOps"])
        self.assertEqual(len(parsed.get("items")), 1)


if __name__ == "__main__":
    unittest.main()


class PromptBuilderTest(unittest.TestCase):
    """Los prompts estaban duplicados en los dos backends y divergieron. Estos
    tests fijan que ambos usan el mismo constructor."""

    def _item(self):
        from datetime import datetime, timezone

        from newsletter_diaria.models import Item

        return Item(
            uid="u1",
            source="Vercel Blog",
            title="GPT-6 now available on AI Gateway",
            link="https://vercel.com/changelog/x",
            published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            summary="Changelog entry",
        )

    def test_ranking_prompt_carries_the_negative_criteria_and_all_uids(self) -> None:
        from newsletter_diaria.llm import build_ranking_prompt

        prompt = build_ranking_prompt({"items": [{"uid": "u1"}, {"uid": "u2"}]})
        self.assertIn("importance 20 o menos", prompt)
        self.assertIn("entradas de changelog", prompt)
        self.assertIn("TODOS los uid exactamente una vez", prompt)
        self.assertIn('"u1"', prompt)
        self.assertIn('"u2"', prompt)
        # El reparto entre fuentes lo imponen las cuotas, no el prompt.
        self.assertNotIn("reparte mejor entre fuentes", prompt)

    def test_summary_prompt_allows_discarding(self) -> None:
        from newsletter_diaria.llm import build_summary_batch_prompt

        prompt = build_summary_batch_prompt({"items": []})
        self.assertIn("descartar", prompt)
        self.assertIn("motivo_descarte", prompt)
        self.assertIn("cadena vacía", prompt)

    def test_both_backends_share_the_ranking_prompt(self) -> None:
        from pathlib import Path
        from unittest.mock import patch

        from newsletter_diaria.llm import (
            OpenAICompatibleProvider,
            OpenCodeProvider,
            build_ranking_prompt,
        )
        from newsletter_diaria.models import OpenAICompatibleConfig, OpenCodeConfig

        items = [self._item()]
        expected_marker = "Criterio único"

        http = OpenAICompatibleProvider(
            OpenAICompatibleConfig(base_url="https://x", api_key="k", api_key_env="K", model="m", json_mode=True)
        )
        with patch.object(http, "_chat_json", return_value={}) as chat:
            http.rank(items)
        self.assertIn(expected_marker, chat.call_args[0][0])

        cli = OpenCodeProvider(
            OpenCodeConfig(cli_command="opencode", model=None, ranker_agent="r", summarizer_agent="s", cwd=Path.cwd())
        )
        with patch.object(cli, "_run_json", return_value={}) as run_json:
            cli.rank(items)
        self.assertIn(expected_marker, run_json.call_args.kwargs["prompt"])

        self.assertIn(expected_marker, build_ranking_prompt({"items": []}))
