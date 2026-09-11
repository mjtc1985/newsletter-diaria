from __future__ import annotations

import logging
import re
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import zip_longest
from datetime import datetime, timezone
from typing import Iterable
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from newsletter_diaria.models import Item, Source
from newsletter_diaria.utils import clean_text, make_uid, parse_datetime

try:
    import certifi
except Exception:  # pragma: no cover
    certifi = None


# Terminos que delatan que un articulo va de IA. Sirven para elegir que aporta
# cada fuente cuando no caben todos sus articulos, no para puntuar ni descartar:
# de eso se encarga el modelo despues. Van sin tildes y en minusculas porque se
# comparan contra el titulo y el resumen del feed, que llegan en ingles.
AI_TERMS = (
    r"artificial intelligence", r"\bai\b", r"\ba\.i\.", r"machine learning", r"\bml\b",
    r"\bllms?\b", r"large language model", r"foundation model", r"frontier model",
    r"generative", r"genai", r"transformer", r"neural", r"diffusion", r"embeddings?",
    r"fine.?tun", r"inference", r"quantiz", r"benchmark", r"hallucinat", r"prompt",
    r"\bagentic\b", r"\bagents?\b", r"copilot", r"chatbot", r"\brag\b",
    r"model context protocol", r"\bmcp\b", r"open.?weight",
    r"openai", r"\bgpt\b", r"chatgpt", r"anthropic", r"claude", r"deepseek", r"gemini",
    r"\bqwen\b", r"mistral", r"hugging ?face", r"llama", r"deepmind", r"nvidia",
)
AI_PATTERN = re.compile("|".join(AI_TERMS), re.I)

UA = "newsletter-diaria/0.1"
FETCH_TIMEOUT_SECONDS = 12
FETCH_ATTEMPTS = 2
MAX_FETCH_WORKERS = 8

logger = logging.getLogger("newsletter_diaria")


def collect_items(sources: Iterable[Source]) -> list[Item]:
    from newsletter_diaria.parsers import resolve_parser

    source_list = list(sources)
    items: list[Item] = []
    total = len(source_list)
    if not source_list:
        return items

    def read_source(index: int, source: Source) -> list[Item]:
        logger.info("[%d/%d] Reading %s", index, total, source.name)
        parser = resolve_parser(source)
        return apply_url_excludes(source, parser.parse(source))

    with ThreadPoolExecutor(max_workers=min(MAX_FETCH_WORKERS, total)) as executor:
        futures = {executor.submit(read_source, index, source): (index, source) for index, source in enumerate(source_list, start=1)}
        for future in as_completed(futures):
            index, source = futures[future]
            try:
                source_items = future.result()
                items.extend(source_items)
                logger.info("[%d/%d] %s OK", index, total, source.name)
            except (URLError, ET.ParseError, TimeoutError) as exc:
                print(f"[warn] {source.name}: {exc}", file=sys.stderr)
            except Exception as exc:  # pragma: no cover - defensive per-source isolation
                print(f"[warn] {source.name}: {exc}", file=sys.stderr)
    return items


def ai_relevance(item: Item) -> int:
    """Cuantos terminos distintos de IA aparecen en el titulo y el resumen."""
    haystack = f"{item.title} {item.summary}"
    return len({match.group(0).lower() for match in AI_PATTERN.finditer(haystack)})


def source_priority(items: list[Item]) -> list[Item]:
    """Orden en que una fuente ofrece sus articulos cuando no caben todos.

    Alterna entre lo que mas parece de IA y lo mas reciente. Solo por recencia,
    una fuente con 50 articulos al dia entregaba los dos ultimos que publico, que
    con este boletin es casi azar. Solo por tema, una noticia ajena a la IA pero
    muy notoria no llegaria nunca al modelo."""
    minimum_date = datetime.min.replace(tzinfo=timezone.utc)

    def stamp(item: Item) -> float:
        return (item.published_at or minimum_date).timestamp()

    by_topic = sorted(items, key=lambda item: (-ai_relevance(item), -stamp(item)))
    by_recency = sorted(items, key=lambda item: -stamp(item))

    ordered: list[Item] = []
    seen: set[str] = set()
    for topical, recent in zip_longest(by_topic, by_recency):
        for candidate in (topical, recent):
            if candidate is not None and candidate.uid not in seen:
                seen.add(candidate.uid)
                ordered.append(candidate)
    return ordered


def apply_url_excludes(source: Source, items: list[Item]) -> list[Item]:
    """Descarta por URL lo que una fuente publica en el mismo feed pero no
    queremos leer (p. ej. las entradas de changelog de Vercel)."""
    if not source.exclude_url_patterns:
        return items
    kept = [
        item
        for item in items
        if not any(pattern in item.link.lower() for pattern in source.exclude_url_patterns)
    ]
    dropped = len(items) - len(kept)
    if dropped:
        logger.info("%s: dropped %d item(s) matching excluded URL patterns", source.name, dropped)
    return kept


def fetch_xml(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    return _fetch_bytes(request)


def fetch_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    return _fetch_bytes(request).decode("utf-8", errors="ignore")


def _fetch_bytes(request: Request) -> bytes:
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    insecure_context = ssl._create_unverified_context()
    last_exc: Exception | None = None

    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS, context=context) as response:
                return response.read()
        except Exception as exc:  # pragma: no cover - depende de red
            last_exc = exc
            reason = getattr(exc, "reason", None)
            if (
                isinstance(exc, ssl.SSLCertVerificationError)
                or isinstance(reason, ssl.SSLCertVerificationError)
                or "CERTIFICATE_VERIFY_FAILED" in str(exc)
            ) and attempt < 2:
                context = insecure_context
                continue
            if attempt < FETCH_ATTEMPTS - 1:
                continue
            raise last_exc

    raise RuntimeError("Could not fetch a response")


def parse_html_source(source: Source) -> list[Item]:
    from newsletter_diaria.parsers.registry import resolve_parser

    return resolve_parser(source).parse(source)


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
        title = extract_between(body, "h2") or extract_between(body, "h4") or extract_between(body, "h3") or full
        summary = extract_between(body, "p") or title
        published_at = parse_datetime(
            extract_between(body, "time") or extract_jsonld_date(body) or extract_jsonld_date(html_text)
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


def parse_deepseek_listing(source: Source, html_text: str) -> list[Item]:
    """DeepSeek no publica RSS: ni /rss.xml ni /feed ni /news/rss.xml existen.
    Su listado de noticias si es HTML estable, con una tarjeta <a> por noticia
    que lleva dentro la fecha, el titular y la entradilla."""
    items: list[Item] = []
    seen: set[str] = set()
    pattern = re.compile(r'<a[^>]+href="(?P<href>/(?:en/)?news/[^"]+)"[^>]*>(?P<body>.*?)</a>', re.I | re.S)
    for match in pattern.finditer(html_text):
        full = urljoin(source.url, match.group("href"))
        if full in seen:
            continue
        body = match.group("body")
        title = extract_between(body, "h2") or extract_between(body, "h3") or extract_between(body, "h4")
        if not title:
            continue
        date_match = re.search(r'text-ds-description[^>]*>([^<]{6,40})<', body, re.I)
        published_at = parse_datetime(clean_text(date_match.group(1))) if date_match else None
        # La entradilla es el ultimo parrafo del bloque: el primero es el rotulo
        # con la seccion y la fecha.
        paragraphs = [clean_text(paragraph) for paragraph in re.findall(r"<p[^>]*>(.*?)</p>", body, re.I | re.S)]
        summary = next((paragraph for paragraph in reversed(paragraphs) if len(paragraph) > 40), title)
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


def extract_candidate_links(base_url: str, html_text: str) -> list[str]:
    base_netloc = urlparse(base_url).netloc
    seen: set[str] = set()
    urls: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html_text, re.I):
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.netloc and parsed.netloc != base_netloc:
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
    import html

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
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def extract_first_paragraph(html_text: str) -> str:
    for paragraph in re.findall(r"<p[^>]*>(.*?)</p>", html_text, re.I | re.S):
        text = clean_text(paragraph)
        if len(text) > 40 and "internet explorer" not in text.lower() and "unsupported" not in text.lower():
            return text
    return ""


def extract_between(html_text: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html_text, re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def extract_jsonld_date(html_text: str) -> str:
    for pattern in (r'"datePublished"\s*:\s*"([^"]+)"', r'"publishedAt"\s*:\s*"([^"]+)"'):
        match = re.search(pattern, html_text, re.I)
        if match:
            return match.group(1).strip()
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
    link = text_of(node, "link") or text_of(node, "guid")
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


def filter_recent(items: Iterable[Item], hours: int) -> list[Item]:
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    return [item for item in items if item.published_at is None or item.published_at.timestamp() >= cutoff]


def dedupe(items: Iterable[Item]) -> list[Item]:
    """Deduplica por enlace normalizado, no por uid: el uid incluye el nombre de
    la fuente, asi que el mismo articulo servido por dos feeds (GitHub Blog y
    GitHub Engineering, por ejemplo) se colaba dos veces."""
    from newsletter_diaria.state import item_key

    seen: set[str] = set()
    unique: list[Item] = []
    for item in items:
        key = item_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def cap_candidates(items: list[Item], limit: int) -> list[Item]:
    """Reparte los huecos por fuente en vez de quedarse con los mas recientes.

    Recortar por recencia entrega el cupo a quien publica mas: con 98
    candidatos y un limite de 30, un agregador se llevaba 15 huecos y siete
    fuentes no llegaban nunca al modelo. Las cuotas editoriales actuan despues
    del ranking, asi que no pueden recuperar lo que se cae aqui."""
    if limit <= 0 or len(items) <= limit:
        return items

    minimum_date = datetime.min.replace(tzinfo=timezone.utc)

    def moment(item: Item) -> datetime:
        return item.published_at or minimum_date

    by_source: dict[str, list[Item]] = {}
    for item in sorted(items, key=moment, reverse=True):
        by_source.setdefault(item.source, []).append(item)
    by_source = {name: source_priority(bucket) for name, bucket in by_source.items()}

    # Las fuentes con la novedad mas fresca van primero, para que en la ultima
    # ronda incompleta entren esas y no las de la cola del alfabeto.
    order = sorted(by_source, key=lambda name: moment(by_source[name][0]), reverse=True)

    selected: list[Item] = []
    depth = 0
    while len(selected) < limit:
        added = False
        for name in order:
            bucket = by_source[name]
            if depth >= len(bucket):
                continue
            selected.append(bucket[depth])
            added = True
            if len(selected) >= limit:
                break
        if not added:
            break
        depth += 1

    return sorted(selected, key=moment, reverse=True)
