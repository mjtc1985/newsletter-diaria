from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    topic: str = "general"
    priority: str = "medium"
    kind: str = "feed"
    max_items: int = 5
    parser: str | None = None


@dataclass(frozen=True)
class Item:
    uid: str
    source: str
    title: str
    link: str
    published_at: datetime | None
    summary: str


@dataclass(frozen=True)
class RankedItem:
    item: Item
    rank: int
    importance: int
    translated_title: str | None
    summary: str
    why: str
    takeaway: str


@dataclass(frozen=True)
class NewsletterDraft:
    headline: str
    items: list[RankedItem]
    trends: list[str]


@dataclass(frozen=True)
class OpenCodeConfig:
    model: str | None
    ranker_agent: str
    summarizer_agent: str
    cwd: Path


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str | None
    api_key: str | None
    api_key_env: str
    model: str | None
    json_mode: bool


@dataclass(frozen=True)
class LLMConfig:
    backend: str
    opencode: OpenCodeConfig
    openai_compatible: OpenAICompatibleConfig


@dataclass(frozen=True)
class AppConfig:
    hours: int
    limit: int
    output: Path
    cache_file: Path
    sources: Path
    ai_mode: str
    ai_candidates: int
    llm: LLMConfig
    send_email: bool
    email_to: str | None
    email_from: str | None
    smtp_username: str | None
    smtp_password: str | None
    smtp_host: str
    smtp_port: int
    smtp_ssl: bool
    test_email: bool
    send_latest: bool
