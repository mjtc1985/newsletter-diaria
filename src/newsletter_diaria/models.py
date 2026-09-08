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
    # Fuentes del mismo dueno comparten cuota: "labs", "cloud", "github"...
    # Vacio significa que la fuente es su propio grupo.
    group: str = ""
    # Subcadenas de URL que se descartan al ingerir (p. ej. "/changelog/").
    exclude_url_patterns: tuple[str, ...] = ()


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
    # La IA puede marcar un articulo como ruido (changelog, caso de cliente,
    # nota promocional) en lugar de inventarle relevancia.
    discarded: bool = False
    discard_reason: str = ""


@dataclass(frozen=True)
class NewsletterDraft:
    headline: str
    items: list[RankedItem]
    trends: list[str]
    # True cuando la importancia sale de la heuristica y no del ranker. Las dos
    # escalas no son comparables, asi que el umbral editorial no se le aplica.
    heuristic_importance: bool = False


@dataclass(frozen=True)
class OpenCodeConfig:
    cli_command: str
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
class EditorialPolicy:
    """Reglas de seleccion que se aplican despues del ranking y los resumenes."""

    min_importance: int
    max_items: int
    max_per_source: int
    max_per_group: int
    # Excepciones al tope por grupo, como pares (grupo, tope). Un dict no vale
    # como default de un dataclass frozen, y asi el orden queda estable.
    group_limits: tuple[tuple[str, int], ...]
    reserved_topics: frozenset[str]
    reserved_slots: int
    relax_floor_if_empty: bool
    relaxed_max_items: int
    # Si la IA no supo decir por que importa un articulo, no entra.
    require_why: bool


DEFAULT_RESERVED_TOPICS = frozenset({"opinion", "research", "security"})

DEFAULT_EDITORIAL_POLICY = EditorialPolicy(
    min_importance=40,
    max_items=10,
    max_per_source=1,
    max_per_group=2,
    # labs a 5 por decision explicita. press a 1: The Register y compania traen
    # 24 articulos en 72h mezclando IA con gadgets, y diluirian la edicion.
    group_limits=(("labs", 5), ("press", 1)),
    reserved_topics=DEFAULT_RESERVED_TOPICS,
    reserved_slots=3,
    relax_floor_if_empty=True,
    relaxed_max_items=3,
    require_why=True,
)


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
    seen_file: Path = Path("output/seen.json")
    seen_retention_days: int = 30
    editorial: EditorialPolicy = DEFAULT_EDITORIAL_POLICY
