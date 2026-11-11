from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from newsletter_diaria.llm import (
    AGY_MAX_PROMPT_BYTES,
    FallbackProvider,
    OpenAICompatibleProvider,
    OpenCodeProvider,
    build_provider,
)
from newsletter_diaria.models import LLMConfig, OpenAICompatibleConfig, OpenCodeConfig


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
        self.assertIn("changelog", prompt.lower())
        # las señales que decidimos a mano sobre una piscina real
        self.assertIn("verificación independiente", prompt)
        self.assertIn("número de versión", prompt)
        self.assertIn("cambiado de decisión", prompt)
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


class AgyCliTest(unittest.TestCase):
    def _provider(self, model: str | None = "gemini-4-argon-high") -> OpenCodeProvider:
        return OpenCodeProvider(
            OpenCodeConfig(cli_command="agy", model=model, ranker_agent="r", summarizer_agent="s", cwd=Path.cwd())
        )

    def test_runs_sandboxed_in_an_empty_dir_and_reads_the_first_json(self) -> None:
        # Respuesta real de agy: el JSON en un bloque ```json y repetido en crudo.
        envelope = {
            "status": "SUCCESS",
            "response": '```json\n{"items": [{"uid": "a", "rank": 1}]}\n```\n{"items":[{"uid":"a","rank":1}]}\n',
        }
        completed = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout=json.dumps(envelope), stderr="")
        with patch("newsletter_diaria.llm.resolve_cli_bin", return_value="agy"), patch(
            "newsletter_diaria.llm.subprocess.run", return_value=completed
        ) as run:
            result = self._provider()._run_json(agent="r", prompt="-rank these")

        self.assertEqual(result, {"items": [{"uid": "a", "rank": 1}]})
        command = run.call_args.args[0]
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--model") + 1], "gemini-4-argon-high")
        self.assertTrue(command[-1].startswith("--print=-rank these"))
        self.assertNotEqual(Path(run.call_args.kwargs["cwd"]), Path.cwd())
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_error_status_raises(self) -> None:
        envelope = {"status": "ERROR", "error": "quota exhausted", "response": ""}
        completed = subprocess.CompletedProcess(args=["agy"], returncode=1, stdout=json.dumps(envelope), stderr="")
        with patch("newsletter_diaria.llm.resolve_cli_bin", return_value="agy"), patch(
            "newsletter_diaria.llm.subprocess.run", return_value=completed
        ):
            with self.assertRaisesRegex(RuntimeError, "quota exhausted"):
                self._provider()._run_json(agent="r", prompt="x")

    def test_timeout_raises_runtime_error(self) -> None:
        with patch("newsletter_diaria.llm.resolve_cli_bin", return_value="agy"), patch(
            "newsletter_diaria.llm.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="agy", timeout=1)
        ):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self._provider()._run_json(agent="r", prompt="x")

    def test_oversized_prompt_fails_before_calling_agy(self) -> None:
        with patch("newsletter_diaria.llm.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "too large"):
                self._provider()._run_json(agent="r", prompt="x" * (AGY_MAX_PROMPT_BYTES + 1))
        run.assert_not_called()


class FallbackProviderTest(unittest.TestCase):
    def _config(self, fallback: str | None) -> LLMConfig:
        return LLMConfig(
            backend="local-cli",
            opencode=OpenCodeConfig(cli_command="agy", model=None, ranker_agent="r", summarizer_agent="s", cwd=Path.cwd()),
            openai_compatible=OpenAICompatibleConfig(base_url="https://x", api_key="k", api_key_env="K", model="m", json_mode=True),
            fallback_backend=fallback,
        )

    def test_without_fallback_the_main_backend_is_used_alone(self) -> None:
        self.assertIsInstance(build_provider(self._config(None)), OpenCodeProvider)

    def test_main_backend_result_is_used_when_it_works(self) -> None:
        provider = build_provider(self._config("openai-compatible"))
        self.assertIsInstance(provider, FallbackProvider)
        with patch.object(provider.primary, "rank", return_value={"from": "agy"}), patch.object(
            provider.fallback, "rank"
        ) as fallback_rank:
            self.assertEqual(provider.rank([]), {"from": "agy"})
        fallback_rank.assert_not_called()

    def test_fallback_repeats_the_call_when_the_main_backend_fails(self) -> None:
        provider = build_provider(self._config("openai-compatible"))
        with patch.object(provider.primary, "summarize_batch", side_effect=RuntimeError("agy failed")), patch.object(
            provider.fallback, "summarize_batch", return_value={"from": "gateway"}
        ) as fallback_call:
            self.assertEqual(provider.summarize_batch([]), {"from": "gateway"})
        fallback_call.assert_called_once_with([])
