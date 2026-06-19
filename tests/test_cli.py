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


if __name__ == "__main__":
    unittest.main()
