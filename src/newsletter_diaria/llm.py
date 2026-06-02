from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import textwrap
from pathlib import Path
from shutil import which as shutil_which
from typing import Protocol
from urllib import error, request

from newsletter_diaria.models import Item, LLMConfig, OpenAICompatibleConfig, OpenCodeConfig

logger = logging.getLogger("newsletter_diaria")


class LLMProvider(Protocol):
    def rank(self, items: list[Item]) -> dict:
        raise NotImplementedError

    def summarize_batch(self, ranked_ids: list[tuple[Item, int, int]]) -> dict:
        raise NotImplementedError

    def summarize_one(self, item: Item) -> dict:
        raise NotImplementedError


class OpenCodeProvider:
    def __init__(self, config: OpenCodeConfig):
        self.config = config

    def rank(self, items: list[Item]) -> dict:
        payload = {
            "items": [
                {
                    "uid": item.uid,
                    "source": item.source,
                    "title": item.title,
                    "link": item.link,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "summary_excerpt": textwrap.shorten(item.summary or item.title, width=180, placeholder="..."),
                }
                for item in items
            ]
        }
        prompt = (
            "Respond in English and ONLY with valid JSON. "
            "Rank these news items by real-world importance. "
            "Use only the provided fields. "
            "Return exactly {\"headline\":string,\"trends\":[string],\"items\":[{\"uid\":string,\"rank\":number,\"importance\":number}]}. "
            f"Data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        logger.info("Ranking prompt length: %d characters", len(prompt))
        logger.info("Calling OpenCode to rank %d items", len(items))
        return self._run_json(agent=self.config.ranker_agent, prompt=prompt)

    def summarize_batch(self, ranked_ids: list[tuple[Item, int, int]]) -> dict:
        payload = {
            "items": [
                {
                    "uid": item.uid,
                    "source": item.source,
                    "title": item.title,
                    "link": item.link,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "summary": item.summary,
                    "rank": rank,
                    "importance": importance,
                }
                for item, rank, importance in ranked_ids
            ]
        }
        prompt = (
            "Respond in English and ONLY with valid JSON. "
            "Summarize ALL articles in the list in a single response. "
            "Make each summary a brief, natural abstract in 2 to 4 sentences, without bullet lists or card-like formatting. "
            "Use only the provided fields. "
            "Return exactly {\"items\":[{\"uid\":string,\"summary\":string,\"why\":string,\"takeaway\":string}]}. "
            f"Data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        logger.info("Calling OpenCode to summarize %d items in batch", len(ranked_ids))
        return self._run_json(agent=self.config.summarizer_agent, prompt=prompt)

    def summarize_one(self, item: Item) -> dict:
        payload = {
            "uid": item.uid,
            "source": item.source,
            "title": item.title,
            "link": item.link,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "summary": item.summary,
        }
        prompt = (
            "Respond in English and ONLY with valid JSON. "
            "Summarize the article without inventing anything. "
            "Write a brief, natural abstract in 2 to 4 sentences, without bullet lists or card-like formatting. "
            "Return exactly {\"summary\":string,\"why\":string,\"takeaway\":string}. "
            f"Data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        return self._run_json(agent=self.config.summarizer_agent, prompt=prompt)

    def _run_json(self, agent: str, prompt: str) -> dict:
        opencode_bin = resolve_opencode_bin()
        command = [opencode_bin, "run", "--agent", agent, "--format", "default", "--dir", str(self.config.cwd)]
        if self.config.model:
            command.extend(["--model", self.config.model])
        command.append(prompt)

        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "opencode failed").strip())
        return extract_json_object(completed.stdout)


class OpenAICompatibleProvider:
    def __init__(self, config: OpenAICompatibleConfig):
        self.config = config
        self.base_url = (config.base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = config.model
        self.api_key = config.api_key or os.getenv(config.api_key_env)

    def rank(self, items: list[Item]) -> dict:
        payload = {
            "items": [
                {
                    "uid": item.uid,
                    "source": item.source,
                    "title": item.title,
                    "link": item.link,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "summary_excerpt": textwrap.shorten(item.summary or item.title, width=180, placeholder="..."),
                }
                for item in items
            ]
        }
        prompt = (
            "Respond in English and ONLY with valid JSON. "
            "Rank these news items by real-world importance. "
            "Use only the provided fields. "
            "Return exactly {\"headline\":string,\"trends\":[string],\"items\":[{\"uid\":string,\"rank\":number,\"importance\":number}]}. "
            f"Data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        logger.info("Calling OpenAI-compatible backend to rank %d items", len(items))
        return self._chat_json(prompt)

    def summarize_batch(self, ranked_ids: list[tuple[Item, int, int]]) -> dict:
        payload = {
            "items": [
                {
                    "uid": item.uid,
                    "source": item.source,
                    "title": item.title,
                    "link": item.link,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "summary": item.summary,
                    "rank": rank,
                    "importance": importance,
                }
                for item, rank, importance in ranked_ids
            ]
        }
        prompt = (
            "Respond in English and ONLY with valid JSON. "
            "Summarize ALL articles in the list in a single response. "
            "Make each summary a brief, natural abstract in 2 to 4 sentences, without bullet lists or card-like formatting. "
            "Use only the provided fields. "
            "Return exactly {\"items\":[{\"uid\":string,\"summary\":string,\"why\":string,\"takeaway\":string}]}. "
            f"Data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        logger.info("Calling OpenAI-compatible backend to summarize %d items in batch", len(ranked_ids))
        return self._chat_json(prompt)

    def summarize_one(self, item: Item) -> dict:
        payload = {
            "uid": item.uid,
            "source": item.source,
            "title": item.title,
            "link": item.link,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "summary": item.summary,
        }
        prompt = (
            "Respond in English and ONLY with valid JSON. "
            "Summarize the article without inventing anything. "
            "Write a brief, natural abstract in 2 to 4 sentences, without bullet lists or card-like formatting. "
            "Return exactly {\"summary\":string,\"why\":string,\"takeaway\":string}. "
            f"Data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        return self._chat_json(prompt)

    def _chat_json(self, prompt: str) -> dict:
        if not self.model:
            raise RuntimeError("Missing model for OpenAI-compatible backend")
        if not self.api_key:
            raise RuntimeError(
                f"Missing API key for OpenAI-compatible backend. Set --llm-api-key or {self.config.api_key_env}"
            )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a precise JSON-only assistant. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OpenAI-compatible backend failed with HTTP {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Could not reach OpenAI-compatible backend: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("OpenAI-compatible backend returned an unexpected response shape") from exc
        content = normalize_content(content)
        return extract_json_object(content)


def build_provider(config: LLMConfig) -> LLMProvider:
    if config.backend == "openai-compatible":
        return OpenAICompatibleProvider(config.openai_compatible)
    return OpenCodeProvider(config.opencode)


def resolve_opencode_bin() -> str:
    if shutil_which("opencode"):
        return "opencode"
    for candidate in (
        Path.home() / ".opencode" / "bin" / "opencode",
        Path.home() / ".local" / "bin" / "opencode",
        Path.home() / ".bun" / "bin" / "opencode",
        Path("/usr/local/bin/opencode"),
        Path("/usr/bin/opencode"),
    ):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError("Could not find the opencode binary")


def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("The AI did not return valid JSON")
    return json.loads(cleaned[start : end + 1])


def normalize_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, dict):
                text = entry.get("text") or entry.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(entry, str):
                parts.append(entry)
        if parts:
            return "\n".join(parts)
    raise RuntimeError("OpenAI-compatible backend returned non-text content")
