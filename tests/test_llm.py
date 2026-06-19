from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from newsletter_diaria.llm import OpenCodeProvider
from newsletter_diaria.models import OpenCodeConfig


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


if __name__ == "__main__":
    unittest.main()
