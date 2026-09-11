from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsletter_diaria.ingest import dedupe
from newsletter_diaria.models import Item
from newsletter_diaria.state import (
    filter_unseen,
    item_key,
    load_seen,
    normalize_link,
    record_seen,
)


def make_item(uid: str, link: str, source: str = "Test") -> Item:
    return Item(uid=uid, source=source, title=f"Title {uid}", link=link, published_at=datetime.now(timezone.utc), summary="s")


class NormalizeLinkTest(unittest.TestCase):
    def test_collapses_scheme_www_trailing_slash_and_tracking(self) -> None:
        variants = [
            "https://github.blog/post-1/",
            "http://www.github.blog/post-1",
            "https://github.blog/post-1?utm_source=rss&utm_medium=feed",
            "https://github.blog/post-1/?ref=newsletter",
        ]
        keys = {normalize_link(url) for url in variants}
        self.assertEqual(len(keys), 1, keys)

    def test_keeps_meaningful_query_params(self) -> None:
        self.assertNotEqual(normalize_link("https://x.dev/p?id=1"), normalize_link("https://x.dev/p?id=2"))

    def test_empty_link_falls_back_to_uid(self) -> None:
        self.assertEqual(item_key(make_item("u1", "")), "u1")


class DedupeTest(unittest.TestCase):
    def test_same_article_from_two_feeds_collapses(self) -> None:
        # El uid incluye el nombre de la fuente, asi que este par tiene uids
        # distintos aunque sea el mismo articulo.
        items = [
            make_item("uid-a", "https://github.blog/post-1/", source="GitHub Blog"),
            make_item("uid-b", "https://github.blog/post-1", source="GitHub Engineering"),
        ]
        self.assertNotEqual(items[0].uid, items[1].uid)
        self.assertEqual(len(dedupe(items)), 1)


class SeenStoreTest(unittest.TestCase):
    def test_roundtrip_and_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.json"
            sent = [make_item("u1", "https://x.dev/a")]
            record_seen(path, sent, {}, retention_days=30)

            seen = load_seen(path)
            self.assertEqual(len(seen), 1)
            candidates = [make_item("u1", "https://www.x.dev/a/"), make_item("u2", "https://x.dev/b")]
            fresh = filter_unseen(candidates, seen)
            self.assertEqual([item.uid for item in fresh], ["u2"])

    def test_prunes_entries_past_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen.json"
            old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
            recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            seen = {"//x.dev/old": old, "//x.dev/recent": recent}
            record_seen(path, [], seen, retention_days=30)

            stored = json.loads(path.read_text(encoding="utf-8"))["items"]
            self.assertNotIn("//x.dev/old", stored)
            self.assertIn("//x.dev/recent", stored)

    def test_missing_or_corrupt_store_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            self.assertEqual(load_seen(missing), {})
            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_seen(broken), {})


if __name__ == "__main__":
    unittest.main()


class CapCandidatesTest(unittest.TestCase):
    def _pool(self) -> list[Item]:
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        items: list[Item] = []
        # Un agregador ruidoso y cuatro fuentes lentas.
        for n in range(20):
            items.append(Item(uid=f"agg{n}", source="Aggregator", title=f"a{n}",
                              link=f"https://agg.dev/{n}", published_at=now - timedelta(minutes=n), summary="s"))
        for name in ("Slow A", "Slow B", "Slow C", "Slow D"):
            items.append(Item(uid=name, source=name, title=name, link=f"https://{name}.dev/1",
                              published_at=now - timedelta(hours=30), summary="s"))
        return items

    def test_shares_slots_across_sources(self) -> None:
        from collections import Counter

        from newsletter_diaria.ingest import cap_candidates

        capped = cap_candidates(self._pool(), 8)
        counts = Counter(item.source for item in capped)
        self.assertEqual(len(capped), 8)
        # Antes el agregador se llevaba los 8 huecos y las lentas no llegaban.
        self.assertEqual(counts["Aggregator"], 4)
        for name in ("Slow A", "Slow B", "Slow C", "Slow D"):
            self.assertEqual(counts[name], 1, name)

    def test_returns_everything_when_under_the_limit(self) -> None:
        from newsletter_diaria.ingest import cap_candidates

        pool = self._pool()
        self.assertEqual(len(cap_candidates(pool, 999)), len(pool))
        self.assertEqual(len(cap_candidates(pool, 0)), len(pool))

    def test_result_stays_ordered_by_recency(self) -> None:
        from newsletter_diaria.ingest import cap_candidates

        capped = cap_candidates(self._pool(), 8)
        stamps = [item.published_at for item in capped]
        self.assertEqual(stamps, sorted(stamps, reverse=True))


class SourcePriorityTest(unittest.TestCase):
    """Dentro de una fuente el reparto era por hora de publicacion, asi que una
    fuente con 50 articulos al dia entregaba los dos ultimos que publico."""

    def _bucket(self):
        from datetime import timedelta

        from newsletter_diaria.models import Item

        now = datetime.now(timezone.utc)
        def mk(n, title, minutes):
            return Item(uid=f"u{n}", source="The Register", title=title, link=f"https://r.dev/{n}",
                        published_at=now - timedelta(minutes=minutes), summary="")
        return [
            mk(1, "Digital Research's GEM opens a new window on Linux", 5),
            mk(2, "Britain reboots its space strategy", 20),
            mk(3, "DeepSeek's new model sets a template for powerful LLMs", 600),
            mk(4, "Anthropic reveals fourth likely crime committed by its AI", 700),
        ]

    def test_ai_relevance_counts_distinct_terms(self) -> None:
        from newsletter_diaria.ingest import ai_relevance

        bucket = {item.uid: item for item in self._bucket()}
        self.assertEqual(ai_relevance(bucket["u1"]), 0)
        self.assertGreater(ai_relevance(bucket["u3"]), 0)
        self.assertGreater(ai_relevance(bucket["u4"]), 0)

    def test_topical_items_come_before_older_noise(self) -> None:
        from newsletter_diaria.ingest import source_priority

        order = [item.uid for item in source_priority(self._bucket())]
        # primero lo mas relevante, luego lo mas reciente, alternando
        self.assertIn(order[0], {"u3", "u4"})
        self.assertEqual(order[1], "u1")
        self.assertEqual(len(order), 4)
        self.assertEqual(len(set(order)), 4)

    def test_a_notable_non_ai_story_still_has_a_path(self) -> None:
        from newsletter_diaria.ingest import cap_candidates

        # con dos huecos entra uno tematico y el mas reciente, no dos tematicos
        chosen = {item.uid for item in cap_candidates(self._bucket(), 2)}
        self.assertIn("u1", chosen)


class DeepSeekParserTest(unittest.TestCase):
    CARD = """
    <a class="ds-news-hero-card" href="/en/news/deepseek-v4-1-flash/">
      <div><img alt="x"/></div>
      <div>
        <p class="ds-text-caption"><span>News</span><span class="text-ds-description">September 10, 2026</span></p>
        <h2 class="ds-text-heading2">Introducing DeepSeek-V4.1-Flash: smarter, faster</h2>
        <p class="ds-text-body-sm">Introducing the smallest model in our new architecture family, with native visual understanding.</p>
      </div>
    </a>
    """

    def test_parses_title_date_and_summary(self) -> None:
        from newsletter_diaria.ingest import parse_deepseek_listing
        from newsletter_diaria.models import Source

        source = Source("DeepSeek", "https://www.deepseek.com/en/news", kind="html", max_items=6, parser="deepseek")
        items = parse_deepseek_listing(source, self.CARD)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn("V4.1-Flash", item.title)
        self.assertEqual(item.link, "https://www.deepseek.com/en/news/deepseek-v4-1-flash/")
        self.assertEqual(item.published_at.date().isoformat(), "2026-09-10")
        self.assertIn("smallest model", item.summary)
        self.assertNotIn("September 10", item.summary)  # el rotulo no es la entradilla

    def test_skips_cards_without_a_title_and_respects_max_items(self) -> None:
        from newsletter_diaria.ingest import parse_deepseek_listing
        from newsletter_diaria.models import Source

        source = Source("DeepSeek", "https://www.deepseek.com/en/news", kind="html", max_items=1, parser="deepseek")
        html = self.CARD + '<a href="/en/news/otra/">sin titular</a>' + self.CARD.replace("v4-1-flash", "v5")
        self.assertEqual(len(parse_deepseek_listing(source, html)), 1)
