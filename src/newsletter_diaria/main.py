from __future__ import annotations

import argparse
import html
import json
import logging
import os
import smtplib
import ssl
import re
import sys
import textwrap
import subprocess
from shutil import which as shutil_which
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

try:
    import certifi
except Exception:  # pragma: no cover
    certifi = None


UA = "newsletter-diaria/0.1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ENV_FILE = PROJECT_ROOT / ".smtpgmail.env"


logger = logging.getLogger("newsletter_diaria")


def load_project_env() -> None:
    loaded_keys: set[str] = set()
    path = PROJECT_ENV_FILE
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if key in os.environ and key not in loaded_keys:
                continue
            os.environ[key] = value
            loaded_keys.add(key)
    except Exception as exc:
        logger.warning("No pude leer %s: %s", path, exc)


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    topic: str = "general"
    priority: str = "medium"
    kind: str = "feed"
    max_items: int = 5


PRIORITY_WEIGHTS = {"high": 4.0, "medium": 2.0, "low": 1.0}


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


DEFAULT_SOURCES: list[Source] = [
    Source("GitHub Blog", "https://github.blog/feed/", topic="software", priority="high"),
    Source("GitHub Engineering", "https://github.blog/category/engineering/feed/", topic="software", priority="high"),
    Source("OpenAI Blog", "https://openai.com/blog/rss.xml", topic="ai", priority="high"),
    Source("Anthropic Blog", "https://www.anthropic.com/news", topic="ai", priority="high", kind="html", max_items=5),
    Source("Google AI Blog", "https://blog.google/technology/ai/rss/", topic="ai", priority="high"),
    Source("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", topic="ai", priority="high"),
    Source("Vercel Blog", "https://vercel.com/blog/feed", topic="software", priority="medium"),
    Source("The Gradient", "https://thegradient.pub/rss/", topic="ai", priority="medium"),
    Source("ByteByteGo", "https://blog.bytebytego.com/feed", topic="software", priority="medium"),
    Source("Cloudflare Blog", "https://blog.cloudflare.com/rss/", topic="infra", priority="medium"),
    Source("AWS Architecture", "https://aws.amazon.com/blogs/architecture/feed/", topic="infra", priority="high"),
    Source("Kubernetes Blog", "https://kubernetes.io/feed.xml", topic="infra", priority="medium"),
    Source("CNCF Blog", "https://www.cncf.io/blog/feed/", topic="infra", priority="medium"),
    Source("HashiCorp Blog", "https://www.hashicorp.com/blog/feed.xml", topic="infra", priority="medium"),
    Source("Uber Engineering", "https://www.uber.com/blog/engineering", topic="software", priority="medium", kind="html", max_items=8),
    Source("Microsoft DevBlogs", "https://devblogs.microsoft.com/feed/", topic="software", priority="medium"),
    Source("Y Combinator Blog", "https://www.ycombinator.com/blog/rss/", topic="opinion", priority="high"),
    Source("Simon Willison", "https://simonwillison.net/atom/entries/", topic="opinion", priority="high"),
    Source("The Pragmatic Engineer", "https://blog.pragmaticengineer.com/rss/", topic="opinion", priority="high"),
    Source("Dan Luu", "https://danluu.com/atom.xml", topic="opinion", priority="high"),
    Source("Martin Fowler", "https://martinfowler.com/feed.atom", topic="opinion", priority="high"),
    Source("Netflix TechBlog", "https://netflixtechblog.com/feed", topic="software", priority="high"),
    Source("Slack Engineering", "https://slack.engineering/feed/", topic="software", priority="high"),
    Source("Facebook Engineering", "https://engineering.fb.com/feed/", topic="software", priority="high"),
    Source("Vlad Mihalcea", "https://vladmihalcea.com/feed/", topic="software", priority="medium"),
    Source("Charity.wtf", "https://charity.wtf/feed/", topic="opinion", priority="medium"),
]

SOURCE_INDEX: dict[str, Source] = {source.name: source for source in DEFAULT_SOURCES}


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="Newsletter diaria por consola")
    parser.add_argument("--hours", type=int, default=24, help="Ventana de horas a incluir")
    parser.add_argument("--limit", type=int, default=0, help="Numero maximo de noticias (0 = sin limite)")
    parser.add_argument("--output", type=Path, default=Path("output/daily.md"), help="Ruta Markdown de salida")
    parser.add_argument("--cache-file", type=Path, default=Path("output/latest.json"), help="Ruta JSON del último boletín generado")
    parser.add_argument("--sources", type=Path, default=Path("sources.json"), help="Ruta a la config de fuentes JSON")
    parser.add_argument("--ai-mode", choices=("auto", "required", "off"), default="auto", help="Uso de IA para ranking y resumen")
    parser.add_argument("--ai-candidates", type=int, default=30, help="Maximo de candidatos enviados a la IA")
    parser.add_argument("--opencode-model", default=os.getenv("NEWSLETTER_OPENCODE_MODEL"), help="Modelo provider/model para opencode")
    parser.add_argument("--opencode-ranker-agent", default="newsletter-ranker", help="Agente opencode para ranking")
    parser.add_argument("--opencode-summarizer-agent", default="newsletter-summarizer", help="Agente opencode para resúmenes")
    parser.add_argument("--opencode-cwd", type=Path, default=Path.cwd(), help="Directorio de trabajo para opencode")
    parser.add_argument("--send-email", action="store_true", help="Enviar la newsletter por Gmail SMTP")
    parser.add_argument("--email-to", default=os.getenv("NEWSLETTER_EMAIL_TO"), help="Destinatario del email")
    parser.add_argument("--email-from", default=os.getenv("NEWSLETTER_EMAIL_FROM") or os.getenv("NEWSLETTER_GMAIL_USER"), help="Remitente Gmail")
    parser.add_argument("--gmail-user", default=os.getenv("NEWSLETTER_GMAIL_USER"), help="Usuario Gmail (si difiere del remitente)")
    parser.add_argument("--gmail-password", default=os.getenv("NEWSLETTER_GMAIL_PASSWORD"), help="Contraseña o app password Gmail")
    parser.add_argument("--smtp-host", default=os.getenv("NEWSLETTER_SMTP_HOST", "smtp.gmail.com"), help="Servidor SMTP")
    parser.add_argument("--smtp-port", type=int, default=int(os.getenv("NEWSLETTER_SMTP_PORT", "465")), help="Puerto SMTP")
    parser.add_argument("--test-email", action="store_true", help="Probar config SMTP sin enviar newsletter")
    parser.add_argument("--send-latest", action="store_true", help="Enviar el último boletín cacheado sin volver a leer feeds")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger.info("Iniciando newsletter diaria")

    logger.info("Cargando fuentes desde %s", args.sources)
    sources = load_sources(args.sources)
    global SOURCE_INDEX
    SOURCE_INDEX = {source.name: source for source in sources}

    if args.test_email:
        try:
            test_email_config(args)
        except RuntimeError as exc:
            print(f"(x) {exc}", file=sys.stderr)
            return 1
        print("Email SMTP OK")
        return 0

    if args.send_latest:
        try:
            draft = load_draft_cache(args.cache_file)
            if not draft.items:
                raise RuntimeError(f"No hay boletín cacheado en {args.cache_file}")
            send_newsletter_email(draft, args)
        except RuntimeError as exc:
            print(f"(x) {exc}", file=sys.stderr)
            return 1
        print(f"\nEnviado desde caché: {args.cache_file}")
        return 0

    logger.info("Leyendo feeds de %d fuentes", len(sources))
    items = collect_items(sources)
    logger.info("Recibidos %d items", len(items))
    items = filter_recent(items, hours=args.hours)
    logger.info("Filtrados a %d items recientes (últimas %d horas)", len(items), args.hours)
    items = dedupe(items)
    logger.info("Tras deduplicar quedan %d items", len(items))
    items = cap_candidates(items, args.ai_candidates)
    logger.info("Candidatos para ranking: %d", len(items))

    try:
        draft = build_newsletter(items, args)
    except RuntimeError as exc:
        print(f"(x) {exc}", file=sys.stderr)
        return 1
    if args.limit > 0:
        draft = NewsletterDraft(headline=draft.headline, items=draft.items[: args.limit], trends=draft.trends)

    if not draft.items:
        print("No se encontraron noticias recientes.")
        return 0

    logger.info("Renderizando salida y escribiendo Markdown en %s", args.output)
    render_console(draft)
    write_markdown(draft, args.output)
    write_draft_cache(draft, args.cache_file)
    if args.send_email:
        try:
            send_newsletter_email(draft, args)
        except RuntimeError as exc:
            print(f"(x) {exc}", file=sys.stderr)
            return 1
    print(f"\nGuardado: {args.output}")
    return 0


def collect_items(sources: Iterable[Source]) -> list[Item]:
    items: list[Item] = []
    for index, source in enumerate(sources, start=1):
        try:
            logger.info("[%d/%d] Leyendo %s", index, len(SOURCE_INDEX), source.name)
            if source.kind == "html":
                items.extend(parse_html_source(source))
            else:
                feed = fetch_xml(source.url)
                items.extend(parse_feed(source.name, feed))
            logger.info("[%d/%d] %s OK", index, len(SOURCE_INDEX), source.name)
        except (URLError, ET.ParseError, TimeoutError) as exc:
            print(f"[warn] {source.name}: {exc}", file=sys.stderr)
    return items


def load_sources(path: Path) -> list[Source]:
    if not path.exists():
        print(f"[warn] No existe {path}, usando fuentes por defecto.", file=sys.stderr)
        return DEFAULT_SOURCES

    data = json.loads(path.read_text(encoding="utf-8"))
    raw_sources = data.get("sources", []) if isinstance(data, dict) else []
    sources: list[Source] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        url = str(raw.get("url", "")).strip()
        topic = str(raw.get("topic", "general")).strip() or "general"
        priority = str(raw.get("priority", "medium")).strip().lower() or "medium"
        kind = str(raw.get("kind", "feed")).strip().lower() or "feed"
        max_items = int(raw.get("max_items", 5) or 5)
        if not name or not url:
            continue
        if priority not in PRIORITY_WEIGHTS:
            priority = "medium"
        if kind not in {"feed", "html"}:
            kind = "feed"
        sources.append(Source(name=name, url=url, topic=topic, priority=priority, kind=kind, max_items=max_items))

    return sources or DEFAULT_SOURCES


def fetch_xml(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    insecure_context = ssl._create_unverified_context()
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20, context=context) as response:
                return response.read()
        except Exception as exc:
            last_exc = exc
            reason = getattr(exc, "reason", None)
            if (
                isinstance(exc, ssl.SSLCertVerificationError)
                or isinstance(reason, ssl.SSLCertVerificationError)
                or "CERTIFICATE_VERIFY_FAILED" in str(exc)
            ) and attempt < 2:
                context = insecure_context
                continue
            if attempt < 2:
                continue
            raise last_exc


def fetch_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    insecure_context = ssl._create_unverified_context()
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20, context=context) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            last_exc = exc
            reason = getattr(exc, "reason", None)
            if (
                isinstance(exc, ssl.SSLCertVerificationError)
                or isinstance(reason, ssl.SSLCertVerificationError)
                or "CERTIFICATE_VERIFY_FAILED" in str(exc)
            ) and attempt < 2:
                context = insecure_context
                continue
            if attempt < 2:
                continue
            raise last_exc


def parse_html_source(source: Source) -> list[Item]:
    listing = fetch_html(source.url)
    if source.name == "Anthropic Blog":
        return parse_anthropic_listing(source, listing)
    if source.name == "Uber Engineering":
        return parse_uber_listing(source, listing)
    candidate_urls = extract_candidate_links(source.url, listing)
    items: list[Item] = []
    for article_url in candidate_urls[: source.max_items]:
        try:
            items.append(parse_html_article(source.name, article_url))
        except Exception as exc:
            logger.warning("[%s] articulo omitido: %s", source.name, exc)
    return items


def parse_anthropic_listing(source: Source, html_text: str) -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'<a[^>]+href="(?P<href>/(?:news|research)/[^"]+)"[^>]*>(?P<body>.*?)</a>',
        re.I | re.S,
    )
    for match in pattern.finditer(html_text):
        href = match.group("href")
        full = urljoin(source.url, href)
        if full in seen:
            continue
        body = match.group("body")
        title = (
            extract_between(body, "h2")
            or extract_between(body, "h4")
            or extract_between(body, "h3")
            or full
        )
        summary = extract_between(body, "p") or title
        published_at = parse_datetime(
            extract_between(body, "time")
            or extract_jsonld_date(body)
            or extract_jsonld_date(html_text)
        )
        items.append(
            Item(
                uid=make_uid(source.name, title, full),
                source=source.name,
                title=clean_text(title),
                link=full,
                published_at=published_at,
                summary=clean_text(summary),
            )
        )
        seen.add(full)
        if len(items) >= source.max_items:
            break
    return items


def parse_uber_listing(source: Source, html_text: str) -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'<div[^>]+data-testid="newsroom-article-feed-card"[^>]*>.*?'
        r'<a[^>]+class="css-gCCMpk"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<div[^>]+class="css-fnbfwD"[^>]*>(?P<date>[^<]+)</div>',
        re.I | re.S,
    )
    for match in pattern.finditer(html_text):
        full = urljoin(source.url, match.group("href"))
        if full in seen:
            continue
        title = clean_text(match.group("title"))
        published_at = parse_datetime(match.group("date"))
        items.append(
            Item(
                uid=make_uid(source.name, title, full),
                source=source.name,
                title=title,
                link=full,
                published_at=published_at,
                summary=title,
            )
        )
        seen.add(full)
        if len(items) >= source.max_items:
            break
    return items


def extract_anthropic_links(base_url: str, html_text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html_text, re.I):
        if not href.startswith(("/news/", "/research/")):
            continue
        full = urljoin(base_url, href)
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def extract_uber_links(base_url: str, html_text: str) -> list[str]:
    start = html_text.find('data-testid="newsroom-article-feed-grid"')
    if start == -1:
        return extract_candidate_links(base_url, html_text)
    chunk = html_text[start:start + 25000]
    seen: set[str] = set()
    urls: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', chunk, re.I):
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if "/blog/" not in parsed.path:
            continue
        if parsed.path.rstrip("/").endswith("/blog/engineering"):
            continue
        if any(segment in parsed.path for segment in ["/blog/engineering/ai", "/blog/engineering/backend", "/blog/engineering/culture", "/blog/engineering/data", "/blog/engineering/mobile", "/blog/engineering/security", "/blog/engineering/web"]):
            continue
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def extract_candidate_links(base_url: str, html_text: str) -> list[str]:
    parsed = urlparse(base_url)
    base_netloc = parsed.netloc
    seen: set[str] = set()
    urls: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html_text, re.I):
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        full = urljoin(base_url, href)
        p = urlparse(full)
        if p.netloc and p.netloc != base_netloc:
            continue
        if full.rstrip("/") == base_url.rstrip("/"):
            continue
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def parse_html_article(source_name: str, article_url: str) -> Item:
    html_text = fetch_html(article_url)
    title = extract_meta(html_text, ["og:title", "twitter:title"]) or extract_title_tag(html_text) or article_url
    summary = extract_meta(html_text, ["description", "og:description", "twitter:description"]) or extract_first_paragraph(html_text)
    published_at = parse_datetime(
        extract_meta(html_text, ["article:published_time", "article:published", "datePublished", "pubdate"])
        or extract_jsonld_date(html_text)
    )
    return Item(
        uid=make_uid(source_name, title, article_url),
        source=source_name,
        title=clean_text(title),
        link=article_url,
        published_at=published_at,
        summary=clean_text(summary),
    )


def extract_meta(html_text: str, keys: list[str]) -> str:
    for key in keys:
        for meta in re.finditer(r"<meta\b[^>]*>", html_text, re.I):
            attrs: dict[str, str] = {}
            for attr_name, _, double_value, single_value in re.findall(r'([A-Za-z_:][\w:.-]*)=("([^"]*)"|\'([^\']*)\')', meta.group(0)):
                attrs[attr_name.lower()] = html.unescape(double_value or single_value)
            prop = attrs.get("property") or attrs.get("name")
            content = attrs.get("content", "")
            if prop == key and content:
                return content.strip()
    return ""


def extract_title_tag(html_text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    return clean_text(m.group(1)) if m else ""


def extract_first_paragraph(html_text: str) -> str:
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html_text, re.I | re.S)
    for para in paragraphs:
        text = clean_text(para)
        if len(text) > 40 and "internet explorer" not in text.lower() and "unsupported" not in text.lower():
            return text
    return ""


def extract_between(html_text: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html_text, re.I | re.S)
    return clean_text(m.group(1)) if m else ""


def extract_jsonld_date(html_text: str) -> str:
    patterns = [
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"publishedAt"\s*:\s*"([^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, html_text, re.I)
        if m:
            return m.group(1).strip()
    return ""


def parse_feed(source_name: str, raw: bytes) -> list[Item]:
    root = ET.fromstring(raw)
    tag = strip_ns(root.tag)
    if tag == "rss":
        channel = root.find("channel")
        if channel is None:
            return []
        return [parse_rss_item(source_name, item) for item in channel.findall("item")]
    if tag == "feed":
        return [parse_atom_item(source_name, entry) for entry in root.findall("{*}entry")]
    return []


def parse_rss_item(source_name: str, node: ET.Element) -> Item:
    title = text_of(node, "title")
    link = text_of(node, "link")
    if not link:
        link = text_of(node, "guid")
    published_at = parse_datetime(text_of(node, "pubDate"))
    summary = clean_text(text_of(node, "description") or text_of(node, "{*}encoded"))
    return Item(uid=make_uid(source_name, title, link), source=source_name, title=title, link=link, published_at=published_at, summary=summary)


def parse_atom_item(source_name: str, node: ET.Element) -> Item:
    title = text_of(node, "{*}title")
    link = ""
    for link_node in node.findall("{*}link"):
        href = link_node.attrib.get("href")
        if href:
            link = href
            break
    published_at = parse_datetime(text_of(node, "{*}published") or text_of(node, "{*}updated"))
    summary = clean_text(text_of(node, "{*}summary") or text_of(node, "{*}content"))
    return Item(uid=make_uid(source_name, title, link), source=source_name, title=title, link=link, published_at=published_at, summary=summary)


def text_of(node: ET.Element, path: str) -> str:
    found = node.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        return dt.astimezone(timezone.utc)
    except Exception:
        for fmt in (
            "%b %d, %Y",
            "%B %d, %Y",
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None


def clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def filter_recent(items: Iterable[Item], hours: int) -> list[Item]:
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    recent: list[Item] = []
    for item in items:
        if item.published_at is None or item.published_at.timestamp() >= cutoff:
            recent.append(item)
    return recent


def dedupe(items: Iterable[Item]) -> list[Item]:
    seen: set[str] = set()
    unique: list[Item] = []
    for item in items:
        key = item.uid
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def normalize(value: str) -> str:
    return re.sub(r"\W+", "", value.lower())


def make_uid(source: str, title: str, link: str) -> str:
    return normalize("|".join([source, title, link]))


def cap_candidates(items: list[Item], limit: int) -> list[Item]:
    if len(items) <= limit:
        return items
    # Preselección suave para no enviar 200 noticias a la IA.
    return sorted(items, key=lambda item: (item.published_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)[:limit]


def build_newsletter(items: list[Item], args: argparse.Namespace) -> NewsletterDraft:
    if not items:
        return NewsletterDraft(headline="", items=[], trends=[])

    if args.ai_mode == "off":
        logger.info("AI desactivada: usando ranking heurístico")
        ranked = heuristic_rank(items)
        return NewsletterDraft(headline="Resumen del día", items=ranked, trends=[])

    try:
        resolve_opencode_bin()
    except RuntimeError:
        msg = "No encuentro opencode en el sistema; usando ranking heurístico."
        if args.ai_mode == "required":
            raise RuntimeError(msg)
        print(f"[warn] {msg}", file=sys.stderr)
        ranked = heuristic_rank(items)
        return NewsletterDraft(headline="Resumen del día", items=ranked, trends=[])

    try:
        logger.info("Usando OpenCode para ranking y resúmenes")
        return opencode_rank_and_summarize(
            items,
            config=OpenCodeConfig(
                model=args.opencode_model,
                ranker_agent=args.opencode_ranker_agent,
                summarizer_agent=args.opencode_summarizer_agent,
                cwd=args.opencode_cwd,
            ),
        )
    except Exception as exc:
        if args.ai_mode == "required":
            raise
        print(f"[warn] opencode falló ({exc}); usando ranking heurístico.", file=sys.stderr)
        ranked = heuristic_rank(items)
        return NewsletterDraft(headline="Resumen del día", items=ranked, trends=[])


def heuristic_rank(items: list[Item]) -> list[RankedItem]:
    ranked_items = sorted(items, key=rank_item, reverse=True)
    result: list[RankedItem] = []
    for idx, item in enumerate(ranked_items, start=1):
        result.append(
            RankedItem(
                item=item,
                rank=idx,
                importance=min(100, int(rank_item(item) * 4)),
                summary=textwrap.shorten(item.summary or item.title, width=240, placeholder="..."),
                why="Ranking heurístico por fecha y fuente.",
                takeaway="Revisión manual recomendada.",
            )
        )
    return result


def opencode_rank_and_summarize(items: list[Item], config: OpenCodeConfig) -> NewsletterDraft:
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
        "Responde en español y SOLO con JSON válido. "
        "Ordena estas noticias por importancia real. "
        "Usa solo los campos dados. "
        "Devuelve exactamente {\"headline\":string,\"trends\":[string],\"items\":[{\"uid\":string,\"rank\":number,\"importance\":number}]}. "
        f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    logger.info("Prompt de ranking: %d caracteres", len(prompt))
    logger.info("Llamando a OpenCode para ranking de %d items", len(items))
    ranking = run_opencode_json(
        config=config,
        agent=config.ranker_agent,
        prompt=prompt,
    )
    ranked_ids = parse_ranking_result(ranking, items)
    logger.info("OpenCode devolvió %d items rankeados", len(ranked_ids))
    summarized = summarize_ranked_items_batch(ranked_ids, config)
    summarized.sort(key=lambda x: (x.rank, -x.importance))
    logger.info("Resumenes completados")
    headline = str(ranking.get("headline", "Resumen del día")).strip() if isinstance(ranking, dict) else "Resumen del día"
    trends = [str(t).strip() for t in (ranking.get("trends", []) if isinstance(ranking, dict) else []) if str(t).strip()]
    return NewsletterDraft(headline=headline or "Resumen del día", items=summarized, trends=trends)


def summarize_ranked_items_batch(ranked_ids: list[tuple[Item, int, int]], config: OpenCodeConfig) -> list[RankedItem]:
    if not ranked_ids:
        return []

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
        "Responde en español y SOLO con JSON válido. "
        "Resume TODOS los artículos de la lista en una sola respuesta. "
        "Haz que cada resumen sea un abstract breve y natural, de 2 a 4 frases, sin formato tipo ficha ni listas. "
        "Usa solo los campos dados. "
        "Devuelve exactamente {\"items\":[{\"uid\":string,\"summary\":string,\"why\":string,\"takeaway\":string}]}. "
        f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    logger.info("Llamando a OpenCode para resumir %d items en batch", len(ranked_ids))
    try:
        data = run_opencode_json(config=config, agent=config.summarizer_agent, prompt=prompt)
        summaries = parse_summary_batch_result(data, ranked_ids)
        if summaries:
            return summaries
    except Exception as exc:
        logger.warning("Batch de resúmenes falló: %s; usando fallback individual", exc)

    summarized: list[RankedItem] = []
    total = len(ranked_ids)
    for index, (item, rank, importance) in enumerate(ranked_ids, start=1):
        logger.info("[%d/%d] Resumiendo individualmente: %s", index, total, item.title)
        summary_data = run_opencode_json(
            config=config,
            agent=config.summarizer_agent,
            prompt=make_summary_prompt(item),
        )
        summarized.append(
            RankedItem(
                item=item,
                rank=rank,
                importance=importance,
                summary=str(summary_data.get("summary", item.summary)).strip(),
                why=str(summary_data.get("why", "")).strip(),
                takeaway=str(summary_data.get("takeaway", "")).strip(),
            )
        )
    return summarized


def make_summary_prompt(item: Item) -> str:
    payload = {
        "uid": item.uid,
        "source": item.source,
        "title": item.title,
        "link": item.link,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "summary": item.summary,
    }
    return (
        "Responde en español y SOLO con JSON válido. "
        "Resume el artículo sin inventar nada. "
        "Escribe un abstract breve y natural, de 2 a 4 frases, sin formato tipo ficha ni listas. "
        "Devuelve exactamente {\"summary\":string,\"why\":string,\"takeaway\":string}. "
        f"Datos: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def parse_summary_batch_result(data: dict, ranked_ids: list[tuple[Item, int, int]]) -> list[RankedItem]:
    by_uid = {item.uid: (item, rank, importance) for item, rank, importance in ranked_ids}
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    result: list[RankedItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        uid = str(raw.get("uid", "")).strip()
        entry = by_uid.get(uid)
        if not entry:
            continue
        item, rank, importance = entry
        result.append(
            RankedItem(
                item=item,
                rank=rank,
                importance=importance,
                summary=str(raw.get("summary", item.summary)).strip(),
                why=str(raw.get("why", "")).strip(),
                takeaway=str(raw.get("takeaway", "")).strip(),
            )
        )
    return result


def run_opencode_json(config: OpenCodeConfig, agent: str, prompt: str) -> dict:
    opencode_bin = resolve_opencode_bin()
    cmd = [opencode_bin, "run", "--agent", agent, "--format", "default", "--dir", str(config.cwd)]
    if config.model:
        cmd.extend(["--model", config.model])
    cmd.append(prompt)
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "opencode falló").strip())
    return extract_json_object(completed.stdout)


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
    raise RuntimeError("No encuentro el binario de opencode")


def parse_ranking_result(data: dict, items: list[Item]) -> list[tuple[Item, int, int]]:
    by_uid = {item.uid: item for item in items}
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    ranked: list[tuple[Item, int, int]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        uid = str(raw.get("uid", "")).strip()
        item = by_uid.get(uid)
        if not item:
            continue
        ranked.append((item, int(raw.get("rank", len(ranked) + 1)), int(raw.get("importance", 50))))
    ranked.sort(key=lambda x: (x[1], -x[2]))
    return ranked


def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("La IA no devolvió JSON válido")
    return json.loads(cleaned[start : end + 1])


def rank_item(item: Item) -> float:
    score = 0.0
    if item.published_at:
        age_hours = (datetime.now(timezone.utc) - item.published_at).total_seconds() / 3600
        score += max(0.0, 24 - age_hours)
    source = SOURCE_INDEX.get(item.source)
    if source:
        score += PRIORITY_WEIGHTS.get(source.priority, 2.0)
        if source.topic in {"ai", "software", "research"}:
            score += 0.5
    else:
        score += 1.0
    score += min(len(item.summary) / 300, 2)
    return score


def render_console(draft: NewsletterDraft) -> None:
    print(f"\n=== {draft.headline} ===\n")
    if draft.trends:
        print("Tendencias:")
        for trend in draft.trends:
            print(f"- {trend}")
        print()
    for ranked in draft.items:
        item = ranked.item
        when = item.published_at.isoformat() if item.published_at else "sin fecha"
        print(f"{ranked.rank}. {item.title}  [{ranked.importance}/100]")
        print(f"   Fuente: {item.source}")
        print(f"   Fecha:  {when}")
        print(f"   Link:   {item.link}")
        print(f"   Res:    {ranked.summary}")
        if ranked.why:
            print(f"   Por qué:{ranked.why}")
        if ranked.takeaway:
            print(f"   Clave:  {ranked.takeaway}")
        print()


def write_markdown(draft: NewsletterDraft, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {draft.headline}", ""]
    if draft.trends:
        lines.extend(["## Tendencias", ""])
        for trend in draft.trends:
            lines.append(f"- {trend}")
        lines.append("")
    for ranked in draft.items:
        item = ranked.item
        when = item.published_at.isoformat() if item.published_at else "sin fecha"
        lines.extend(
            [
                f"{ranked.rank}. **{item.title}**  _[{ranked.importance}/100]_",
                f"   - Fuente: {item.source}",
                f"   - Fecha: {when}",
                f"   - Link: {item.link}",
                f"   - Resumen: {ranked.summary or '—'}",
                f"   - Por qué: {ranked.why or '—'}",
                f"   - Clave: {ranked.takeaway or '—'}",
                "",
            ]
        )
    output.write_text("\n".join(lines), encoding="utf-8")


def write_draft_cache(draft: NewsletterDraft, cache_file: Path) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "headline": draft.headline,
        "trends": draft.trends,
        "items": [
            {
                "uid": ranked.item.uid,
                "source": ranked.item.source,
                "title": ranked.item.title,
                "link": ranked.item.link,
                "published_at": ranked.item.published_at.isoformat() if ranked.item.published_at else None,
                "summary": ranked.item.summary,
                "rank": ranked.rank,
                "importance": ranked.importance,
                "summary_ai": ranked.summary,
                "why": ranked.why,
                "takeaway": ranked.takeaway,
            }
            for ranked in draft.items
        ],
    }
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_draft_cache(cache_file: Path) -> NewsletterDraft:
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Caché inválida: {cache_file}")
    items_data = data.get("items", [])
    ranked_items: list[RankedItem] = []
    for raw in items_data:
        if not isinstance(raw, dict):
            continue
        item = Item(
            uid=str(raw.get("uid", "")),
            source=str(raw.get("source", "")),
            title=str(raw.get("title", "")),
            link=str(raw.get("link", "")),
            published_at=parse_datetime(str(raw.get("published_at", ""))) if raw.get("published_at") else None,
            summary=str(raw.get("summary", "")),
        )
        ranked_items.append(
            RankedItem(
                item=item,
                rank=int(raw.get("rank", len(ranked_items) + 1)),
                importance=int(raw.get("importance", 50)),
                summary=str(raw.get("summary_ai", raw.get("summary", ""))),
                why=str(raw.get("why", "")),
                takeaway=str(raw.get("takeaway", "")),
            )
        )
    ranked_items.sort(key=lambda x: (x.rank, -x.importance))
    return NewsletterDraft(
        headline=str(data.get("headline", "Resumen del día")),
        trends=[str(x) for x in data.get("trends", []) if str(x).strip()],
        items=ranked_items,
    )


def send_newsletter_email(draft: NewsletterDraft, args: argparse.Namespace) -> None:
    recipients = parse_recipients(args.email_to)
    if not recipients:
        raise RuntimeError("Falta --email-to o NEWSLETTER_EMAIL_TO")
    if not args.gmail_password:
        raise RuntimeError("Falta --gmail-password o NEWSLETTER_GMAIL_PASSWORD")

    sender = args.email_from or args.gmail_user
    if not sender:
        raise RuntimeError("Falta --email-from o NEWSLETTER_EMAIL_FROM")

    subject = draft.headline or "Resumen del día"
    text_body = render_email_text(draft)
    html_body = render_email_html(draft)

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    logger.info("Enviando email a %s via %s:%s", ", ".join(recipients), args.smtp_host, args.smtp_port)
    with smtplib.SMTP_SSL(args.smtp_host, args.smtp_port, timeout=30) as client:
        client.login(args.gmail_user or sender, args.gmail_password)
        client.send_message(message, to_addrs=recipients)
    logger.info("Email enviado")


def test_email_config(args: argparse.Namespace) -> None:
    if not parse_recipients(args.email_to):
        raise RuntimeError("Falta --email-to o NEWSLETTER_EMAIL_TO")
    if not args.gmail_password:
        raise RuntimeError("Falta --gmail-password o NEWSLETTER_GMAIL_PASSWORD")

    sender = args.email_from or args.gmail_user
    if not sender:
        raise RuntimeError("Falta --email-from o NEWSLETTER_EMAIL_FROM")

    logger.info("Probando SMTP contra %s:%s", args.smtp_host, args.smtp_port)
    with smtplib.SMTP_SSL(args.smtp_host, args.smtp_port, timeout=30) as client:
        client.login(args.gmail_user or sender, args.gmail_password)


def parse_recipients(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[;,\n]+", value)
    recipients = []
    seen = set()
    for part in parts:
        addr = part.strip()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        recipients.append(addr)
    return recipients


def render_email_text(draft: NewsletterDraft) -> str:
    lines = [draft.headline or "Resumen del día", ""]
    if draft.trends:
        lines.append("Tendencias:")
        for trend in draft.trends:
            lines.append(f"- {trend}")
        lines.append("")
    for ranked in draft.items:
        item = ranked.item
        when = item.published_at.isoformat() if item.published_at else "sin fecha"
        lines.extend(
            [
                f"{ranked.rank}. {item.title} [{ranked.importance}/100]",
                f"Fuente: {item.source}",
                f"Fecha: {when}",
                f"Link: {item.link}",
                f"Resumen: {ranked.summary or '—'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_email_html(draft: NewsletterDraft) -> str:
    def esc(value: str) -> str:
        return html.escape(value or "")

    def fmt_dt(item: Item) -> str:
        return item.published_at.isoformat() if item.published_at else "sin fecha"

    trend_html = ""
    if draft.trends:
        trend_html = """
        <div class="trends">
          <h2>Tendencias</h2>
          <ul>
            {trends}
          </ul>
        </div>
        """.format(trends="".join(f"<li>{esc(trend)}</li>" for trend in draft.trends))

    cards = []
    for ranked in draft.items:
        item = ranked.item
        cards.append(
            f"""
            <tr>
              <td class="card">
                <div class="meta">
                  <span class="badge">#{ranked.rank}</span>
                  <span class="score">{ranked.importance}/100</span>
                </div>
                <h3><a href="{esc(item.link)}">{esc(item.title)}</a></h3>
                <p class="source">{esc(item.source)} · {esc(fmt_dt(item))}</p>
                <p class="summary">{esc(ranked.summary or item.summary or "—")}</p>
              </td>
            </tr>
            """
        )

    cards_html = "".join(cards)
    return f"""<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{esc(draft.headline or 'Resumen del día')}</title>
    <style>
      body {{ margin: 0; padding: 0; background: #f4f7fb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #0f172a; }}
      .wrap {{ width: 100%; padding: 32px 0; }}
      .container {{ max-width: 760px; margin: 0 auto; background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 8px 30px rgba(15, 23, 42, 0.08); }}
      .hero {{ padding: 32px 32px 20px; background: linear-gradient(135deg, #0f172a, #1d4ed8); color: #fff; }}
      .hero h1 {{ margin: 0; font-size: 28px; line-height: 1.2; }}
      .hero p {{ margin: 10px 0 0; opacity: 0.9; }}
      .content {{ padding: 24px 24px 12px; }}
      .trends {{ margin-bottom: 24px; padding: 18px; background: #eff6ff; border-radius: 14px; }}
      .trends h2 {{ margin: 0 0 10px; font-size: 18px; }}
      .trends ul {{ margin: 0; padding-left: 20px; }}
      table {{ width: 100%; border-collapse: collapse; }}
      .card {{ padding: 18px; border: 1px solid #e2e8f0; border-radius: 14px; margin-bottom: 14px; background: #fff; }}
      .meta {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
      .badge {{ display: inline-block; background: #dbeafe; color: #1d4ed8; font-weight: 700; font-size: 12px; padding: 4px 8px; border-radius: 999px; }}
      .score {{ font-size: 12px; color: #64748b; font-weight: 700; }}
      h3 {{ margin: 0 0 8px; font-size: 20px; line-height: 1.25; }}
      h3 a {{ color: #0f172a; text-decoration: none; }}
      .source {{ margin: 0 0 12px; color: #64748b; font-size: 13px; }}
      .summary {{ margin: 0 0 14px; color: #334155; line-height: 1.55; }}
      .footer {{ padding: 18px 24px 28px; color: #64748b; font-size: 12px; text-align: center; }}
      @media (max-width: 640px) {{
        .hero, .content {{ padding-left: 16px; padding-right: 16px; }}
      }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="container">
        <div class="hero">
          <h1>{esc(draft.headline or 'Resumen del día')}</h1>
          <p>Selección de lo más relevante de las últimas 24 horas.</p>
        </div>
        <div class="content">
          {trend_html}
          <table role="presentation">
            <tbody>
              {cards_html}
            </tbody>
          </table>
        </div>
        <div class="footer">Generado automáticamente por newsletter-diaria</div>
      </div>
    </div>
  </body>
</html>"""


if __name__ == "__main__":
    raise SystemExit(main())
