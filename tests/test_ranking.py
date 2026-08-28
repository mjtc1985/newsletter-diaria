from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from newsletter_diaria.models import Item, RankedItem
from newsletter_diaria.ranking import (
    SUMMARY_CHUNK_SIZE,
    parse_summary_batch_result,
    summarize_ranked_items_batch,
)


class RankingAndSummariesTest(unittest.TestCase):
    def test_parse_summary_batch_result(self) -> None:
        items = [
            (
                Item(
                    uid="u1",
                    source="Test",
                    title="English Title 1",
                    link="http://example.com/1",
                    published_at=datetime.now(timezone.utc),
                    summary="Raw summary 1",
                ),
                1,
                90,
            ),
            (
                Item(
                    uid="u2",
                    source="Test",
                    title="English Title 2",
                    link="http://example.com/2",
                    published_at=datetime.now(timezone.utc),
                    summary="Raw summary 2",
                ),
                2,
                80,
            ),
        ]
        data = {
            "items": [
                {
                    "uid": "u1",
                    "title": "Título en español 1",
                    "summary": "Resumen en español 1",
                    "why": "Por qué 1",
                    "takeaway": "Takeaway 1",
                },
                {
                    "uid": "u2",
                    "title": "Título en español 2",
                    "summary": "Resumen en español 2",
                    "why": "Por qué 2",
                    "takeaway": "Takeaway 2",
                },
            ]
        }
        results = parse_summary_batch_result(data, items)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].translated_title, "Título en español 1")
        self.assertEqual(results[0].summary, "Resumen en español 1")
        self.assertEqual(results[1].translated_title, "Título en español 2")

    def test_summarize_ranked_items_batch_chunks_and_calls_provider(self) -> None:
        items = [
            (
                Item(
                    uid=f"u{i}",
                    source="Test",
                    title=f"English Title {i}",
                    link=f"http://example.com/{i}",
                    published_at=datetime.now(timezone.utc),
                    summary=f"Raw summary {i}",
                ),
                i,
                100 - i,
            )
            for i in range(1, 13)
        ]

        provider = MagicMock()
        def fake_summarize_batch(chunk):
            return {
                "items": [
                    {
                        "uid": item.uid,
                        "title": f"Título traducido {item.uid}",
                        "summary": f"Resumen {item.uid}",
                        "why": "why",
                        "takeaway": "takeaway",
                    }
                    for item, _, _ in chunk
                ]
            }

        provider.summarize_batch.side_effect = fake_summarize_batch

        results = summarize_ranked_items_batch(items, provider)
        self.assertEqual(len(results), 12)
        self.assertEqual(provider.summarize_batch.call_count, 3)
        self.assertEqual(results[0].translated_title, "Título traducido u1")
        self.assertEqual(results[11].translated_title, "Título traducido u12")

    def test_summarize_ranked_items_batch_recovers_missing_items_individually(self) -> None:
        item1 = Item(
            uid="u1",
            source="Test",
            title="English Title 1",
            link="http://example.com/1",
            published_at=datetime.now(timezone.utc),
            summary="Raw summary 1",
        )
        item2 = Item(
            uid="u2",
            source="Test",
            title="English Title 2",
            link="http://example.com/2",
            published_at=datetime.now(timezone.utc),
            summary="Raw summary 2",
        )
        items = [(item1, 1, 90), (item2, 2, 80)]

        provider = MagicMock()
        provider.summarize_batch.return_value = {
            "items": [
                {
                    "uid": "u1",
                    "title": "Título 1",
                    "summary": "Resumen 1",
                    "why": "why 1",
                    "takeaway": "takeaway 1",
                }
            ]
        }
        provider.summarize_one.return_value = {
            "title": "Título 2 fallback",
            "summary": "Resumen 2 fallback",
            "why": "why 2",
            "takeaway": "takeaway 2",
        }

        results = summarize_ranked_items_batch(items, provider)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].translated_title, "Título 1")
        self.assertEqual(results[1].translated_title, "Título 2 fallback")
        self.assertEqual(results[1].summary, "Resumen 2 fallback")
        provider.summarize_one.assert_called_once_with(item2)

    def test_llm_rank_and_summarize_recovers_from_rank_failure(self) -> None:
        from newsletter_diaria.models import LLMConfig, OpenAICompatibleConfig, OpenCodeConfig
        from newsletter_diaria.ranking import llm_rank_and_summarize
        from pathlib import Path

        item = Item(
            uid="u1",
            source="Test",
            title="English Title",
            link="http://example.com/1",
            published_at=datetime.now(timezone.utc),
            summary="Raw summary",
        )
        config = LLMConfig(
            backend="openai-compatible",
            opencode=OpenCodeConfig(cli_command="opencode", model=None, ranker_agent="r", summarizer_agent="s", cwd=Path.cwd()),
            openai_compatible=OpenAICompatibleConfig(base_url="http://example.com", api_key="k", api_key_env="K", model="m", json_mode=True),
        )

        mock_provider = MagicMock()
        mock_provider.rank.side_effect = RuntimeError("Ranking failed")
        mock_provider.summarize_batch.return_value = {
            "items": [
                {
                    "uid": "u1",
                    "title": "Título traducido",
                    "summary": "Resumen IA",
                    "why": "Importancia IA",
                    "takeaway": "Conclusión IA",
                }
            ]
        }

        with patch("newsletter_diaria.ranking.build_provider", return_value=mock_provider):
            draft = llm_rank_and_summarize([item], config, {})

        self.assertEqual(len(draft.items), 1)
        self.assertEqual(draft.items[0].translated_title, "Título traducido")
        self.assertEqual(draft.items[0].summary, "Resumen IA")


if __name__ == "__main__":
    unittest.main()
