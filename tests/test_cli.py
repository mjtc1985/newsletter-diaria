from __future__ import annotations

import os
import unittest

from newsletter_diaria.cli import parse_args


class CliParsingTest(unittest.TestCase):
    def test_local_cli_defaults_to_opencode(self) -> None:
        original = os.environ.get("NEWSLETTER_LLM_CLI_COMMAND")
        try:
            os.environ.pop("NEWSLETTER_LLM_CLI_COMMAND", None)
            config = parse_args([])
            self.assertEqual(config.llm.opencode.cli_command, "opencode")
            self.assertIsNone(config.llm.opencode.model)
        finally:
            if original is None:
                os.environ.pop("NEWSLETTER_LLM_CLI_COMMAND", None)
            else:
                os.environ["NEWSLETTER_LLM_CLI_COMMAND"] = original

    def test_agy_keeps_its_model_and_the_fallback_backend(self) -> None:
        config = parse_args([
            "--llm-backend", "local-cli",
            "--llm-cli-command", "agy",
            "--local-cli-model", "gemini-4-argon-high",
            "--llm-fallback-backend", "openai-compatible",
        ])
        self.assertEqual(config.llm.opencode.cli_command, "agy")
        self.assertEqual(config.llm.opencode.model, "gemini-4-argon-high")
        self.assertEqual(config.llm.fallback_backend, "openai-compatible")

    def test_no_fallback_by_default(self) -> None:
        original = os.environ.pop("NEWSLETTER_LLM_FALLBACK_BACKEND", None)
        try:
            self.assertIsNone(parse_args([]).llm.fallback_backend)
        finally:
            if original is not None:
                os.environ["NEWSLETTER_LLM_FALLBACK_BACKEND"] = original


if __name__ == "__main__":
    unittest.main()
