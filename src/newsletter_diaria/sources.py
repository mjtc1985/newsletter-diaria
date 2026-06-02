from __future__ import annotations

import json
import sys
from pathlib import Path

from newsletter_diaria.models import Source


PRIORITY_WEIGHTS = {"high": 4.0, "medium": 2.0, "low": 1.0}


DEFAULT_SOURCES: list[Source] = [
    Source("GitHub Blog", "https://github.blog/feed/", topic="software", priority="high", parser="feed"),
    Source("GitHub Engineering", "https://github.blog/category/engineering/feed/", topic="software", priority="high", parser="feed"),
    Source("OpenAI Blog", "https://openai.com/blog/rss.xml", topic="ai", priority="high", parser="feed"),
    Source("Anthropic Blog", "https://www.anthropic.com/news", topic="ai", priority="high", kind="html", max_items=5, parser="anthropic"),
    Source("Google AI Blog", "https://blog.google/technology/ai/rss/", topic="ai", priority="high", parser="feed"),
    Source("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", topic="ai", priority="high", parser="feed"),
    Source("Vercel Blog", "https://vercel.com/blog/feed", topic="software", priority="medium", parser="feed"),
    Source("The Gradient", "https://thegradient.pub/rss/", topic="ai", priority="medium", parser="feed"),
    Source("ByteByteGo", "https://blog.bytebytego.com/feed", topic="software", priority="medium", parser="feed"),
    Source("Cloudflare Blog", "https://blog.cloudflare.com/rss/", topic="infra", priority="medium", parser="feed"),
    Source("AWS Architecture", "https://aws.amazon.com/blogs/architecture/feed/", topic="infra", priority="high", parser="feed"),
    Source("Kubernetes Blog", "https://kubernetes.io/feed.xml", topic="infra", priority="medium", parser="feed"),
    Source("CNCF Blog", "https://www.cncf.io/blog/feed/", topic="infra", priority="medium", parser="feed"),
    Source("HashiCorp Blog", "https://www.hashicorp.com/blog/feed.xml", topic="infra", priority="medium", parser="feed"),
    Source("Uber Engineering", "https://www.uber.com/blog/engineering", topic="software", priority="medium", kind="html", max_items=8, parser="uber_engineering"),
    Source("Microsoft DevBlogs", "https://devblogs.microsoft.com/feed/", topic="software", priority="medium", parser="feed"),
    Source("Y Combinator Blog", "https://www.ycombinator.com/blog/rss/", topic="opinion", priority="high", parser="feed"),
    Source("Simon Willison", "https://simonwillison.net/atom/entries/", topic="opinion", priority="high", parser="feed"),
    Source("The Pragmatic Engineer", "https://blog.pragmaticengineer.com/rss/", topic="opinion", priority="high", parser="feed"),
    Source("Dan Luu", "https://danluu.com/atom.xml", topic="opinion", priority="high", parser="feed"),
    Source("Martin Fowler", "https://martinfowler.com/feed.atom", topic="opinion", priority="high", parser="feed"),
    Source("Netflix TechBlog", "https://netflixtechblog.com/feed", topic="software", priority="high", parser="feed"),
    Source("Slack Engineering", "https://slack.engineering/feed/", topic="software", priority="high", parser="feed"),
    Source("Facebook Engineering", "https://engineering.fb.com/feed/", topic="software", priority="high", parser="feed"),
    Source("Vlad Mihalcea", "https://vladmihalcea.com/feed/", topic="software", priority="medium", parser="feed"),
    Source("Charity.wtf", "https://charity.wtf/feed/", topic="opinion", priority="medium", parser="feed"),
]


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
        parser = str(raw.get("parser", "")).strip() or None
        if not name or not url:
            continue
        if priority not in PRIORITY_WEIGHTS:
            priority = "medium"
        if kind not in {"feed", "html"}:
            kind = "feed"
        sources.append(Source(name=name, url=url, topic=topic, priority=priority, kind=kind, max_items=max_items, parser=parser))

    return sources or DEFAULT_SOURCES


def sources_by_name(sources: list[Source]) -> dict[str, Source]:
    return {source.name: source for source in sources}
