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
