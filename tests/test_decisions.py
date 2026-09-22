from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from newsletter_diaria.decisions import Judgement, TypeSafeDecider, state_of
from newsletter_diaria.models import Item


def item(uid: str = "u1", body: str = "") -> Item:
    return Item(uid=uid, source="InfoQ", title="Un titular", link="https://x.dev/1",
                published_at=datetime.now(timezone.utc), summary="resumen del feed", body=body)


class JudgementTest(unittest.TestCase):
    def test_verifiability_pulls_a_vendor_claim_down(self) -> None:
        vendor = Judgement(consequence=4.5, verifiability=1.0, is_ai=.9, commercial=.1)
        checked = Judgement(consequence=4.5, verifiability=4.5, is_ai=.9, commercial=.1)
        self.assertLess(vendor.importance, checked.importance)
        self.assertEqual((vendor.importance, checked.importance), (69, 90))

    def test_importance_stays_inside_the_1_to_100_scale(self) -> None:
        self.assertEqual(Judgement(0, 0, 0, 0).importance, 1)
        self.assertEqual(Judgement(5, 5, 1, 0).importance, 100)

    def test_subject_and_discard_thresholds(self) -> None:
        self.assertEqual(Judgement(3, 3, is_ai=.5, commercial=0).subject, "ia")
        self.assertEqual(Judgement(3, 3, is_ai=.49, commercial=0).subject, "otro")
        self.assertTrue(Judgement(3, 3, is_ai=.9, commercial=.6).discarded)
        self.assertFalse(Judgement(3, 3, is_ai=.9, commercial=.59).discarded)


class StateTest(unittest.TestCase):
    def test_prefers_the_article_body_over_the_feed_text(self) -> None:
        self.assertEqual(state_of(item(body="cuerpo real"))["texto"], "cuerpo real")
        self.assertEqual(state_of(item())["texto"], "resumen del feed")

    def test_caps_the_text_it_sends(self) -> None:
        self.assertLessEqual(len(state_of(item(body="x" * 9000))["texto"]), 2200)


class DeciderTest(unittest.TestCase):
    def _decider(self) -> TypeSafeDecider:
        return TypeSafeDecider(api_key="k")

    def test_judge_maps_the_answers(self) -> None:
        decider = self._decider()
        answers = {
            "es_ia": {"noul": 0.8}, "comercial": {"noul": 0.1},
            "consecuencia": {"score": 4.0}, "verificabilidad": {"score": 3.0},
        }
        with patch.object(decider, "ask", return_value=answers):
            result = decider.judge([item()])
        self.assertEqual(result["u1"].consequence, 4.0)
        self.assertEqual(result["u1"].subject, "ia")

    def test_a_failing_call_drops_that_item_only(self) -> None:
        decider = self._decider()
        calls = {"n": 0}

        def flaky(state, questions):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("503")
            return {"es_ia": {"noul": .9}, "comercial": {"noul": .1},
                    "consecuencia": {"score": 3.0}, "verificabilidad": {"score": 3.0}}

        with patch.object(decider, "ask", side_effect=flaky):
            result = decider.judge([item("u1"), item("u2")])
        self.assertEqual(set(result), {"u2"})

    def test_worth_reading_returns_a_probability_per_item(self) -> None:
        decider = self._decider()
        with patch.object(decider, "ask", return_value={"merece": {"noul": 0.73}}):
            self.assertEqual(decider.worth_reading([item()]), {"u1": 0.73})


class RetryTest(unittest.TestCase):
    def test_a_transient_failure_is_retried(self) -> None:
        decider = TypeSafeDecider(api_key="k")
        calls = {"n": 0}

        def flaky(state, questions):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("Could not reach TypeSafe: name resolution")
            return {"merece": {"noul": 0.9}}

        with patch.object(decider, "ask_once", side_effect=flaky), \
             patch("newsletter_diaria.decisions.time.sleep"):
            self.assertEqual(decider.worth_reading([item()]), {"u1": 0.9})
        self.assertEqual(calls["n"], 3)

    def test_it_gives_up_after_the_last_attempt(self) -> None:
        decider = TypeSafeDecider(api_key="k")
        with patch.object(decider, "ask_once", side_effect=RuntimeError("503")), \
             patch("newsletter_diaria.decisions.time.sleep"):
            self.assertEqual(decider.worth_reading([item()]), {})


class PreselectionFallbackTest(unittest.TestCase):
    def _items(self, n: int = 100):
        return [Item(uid=f"u{i}", source=f"S{i % 5}", title=f"T{i}", link=f"https://x/{i}",
                     published_at=datetime.now(timezone.utc), summary="s") for i in range(1, n + 1)]

    def _config(self):
        from pathlib import Path

        from newsletter_diaria.models import LLMConfig, OpenAICompatibleConfig, OpenCodeConfig

        return LLMConfig(backend="openai-compatible",
                         opencode=OpenCodeConfig(cli_command="opencode", model=None, ranker_agent="r", summarizer_agent="s", cwd=Path.cwd()),
                         openai_compatible=OpenAICompatibleConfig(base_url="http://x", api_key="k", api_key_env="K", model="m", json_mode=True))

    def test_uses_the_decision_model_when_it_answers_for_everyone(self) -> None:
        from newsletter_diaria.ranking import preselect_candidates

        items = self._items()
        decider = MagicMock()
        decider.worth_reading.return_value = {f"u{i}": i / 100 for i in range(1, 101)}
        chosen = preselect_candidates(items, 10, self._config(), decider)
        self.assertEqual([i.uid for i in chosen], [f"u{i}" for i in range(100, 90, -1)])

    def test_a_partial_answer_falls_back_to_the_language_model(self) -> None:
        from newsletter_diaria.ranking import preselect_candidates

        items = self._items()
        decider = MagicMock()
        decider.worth_reading.return_value = {"u1": 0.9, "u2": 0.8}
        provider = MagicMock()
        provider.preselect.return_value = {"elegidos": list(range(1, 11))}
        with patch("newsletter_diaria.ranking.build_provider", return_value=provider):
            chosen = preselect_candidates(items, 10, self._config(), decider)
        provider.preselect.assert_called_once()
        self.assertEqual(len(chosen), 10)

    def test_a_raising_decider_falls_back_too(self) -> None:
        from newsletter_diaria.ranking import preselect_candidates

        decider = MagicMock()
        decider.worth_reading.side_effect = RuntimeError("timeout")
        provider = MagicMock()
        provider.preselect.return_value = {"elegidos": list(range(1, 11))}
        with patch("newsletter_diaria.ranking.build_provider", return_value=provider):
            chosen = preselect_candidates(self._items(), 10, self._config(), decider)
        self.assertEqual(len(chosen), 10)
