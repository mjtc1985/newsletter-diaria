from __future__ import annotations

import unittest
from datetime import datetime, timezone

from newsletter_diaria.editorial import select_items
from newsletter_diaria.models import EditorialPolicy, Item, RankedItem, Source

POLICY = EditorialPolicy(
    min_importance=40,
    max_items=10,
    max_per_source=1,
    max_per_group=2,
    group_limits=(),
    reserved_topics=frozenset({"opinion", "research", "security"}),
    reserved_slots=3,
    relax_floor_if_empty=True,
    relaxed_max_items=3,
    require_why=True,
)

SOURCES = {
    source.name: source
    for source in [
        Source("OpenAI Blog", "u", topic="ai", group="labs"),
        Source("Anthropic Blog", "u", topic="ai", group="labs"),
        Source("Google DeepMind", "u", topic="ai", group="labs"),
        Source("Vercel Blog", "u", topic="software", group="cloud"),
        Source("Cloudflare Blog", "u", topic="infra", group="cloud"),
        Source("Dan Luu", "u", topic="opinion"),
        Source("Martin Fowler", "u", topic="opinion"),
        Source("Krebs", "u", topic="security"),
    ]
}


def ranked(source: str, rank: int, importance: int = 80, *, discarded: bool = False,
           uid: str | None = None, why: str = "porque importa") -> RankedItem:
    item = Item(
        uid=uid or f"{source}-{rank}",
        source=source,
        title=f"{source} #{rank}",
        link=f"https://example.com/{source}/{rank}".replace(" ", "-"),
        published_at=datetime.now(timezone.utc),
        summary="s",
    )
    return RankedItem(
        item=item,
        rank=rank,
        importance=importance,
        translated_title=None,
        summary="resumen",
        why=why,
        takeaway="",
        discarded=discarded,
        discard_reason="entrada de changelog" if discarded else "",
    )


def names(result) -> list[str]:
    return [item.item.source for item in result.items]


class EditorialPolicyTest(unittest.TestCase):
    def test_drops_items_the_ai_discarded(self) -> None:
        result = select_items(
            [ranked("Vercel Blog", 1, discarded=True), ranked("Dan Luu", 2)],
            POLICY,
            SOURCES,
        )
        self.assertEqual(names(result), ["Dan Luu"])
        self.assertIn("descartado por la IA: entrada de changelog", result.rejected[0][1])

    def test_drops_items_below_the_importance_floor(self) -> None:
        result = select_items(
            [ranked("Vercel Blog", 1, importance=20), ranked("Dan Luu", 2, importance=55)],
            POLICY,
            SOURCES,
        )
        self.assertEqual(names(result), ["Dan Luu"])
        self.assertIn("por debajo del umbral", result.rejected[0][1])

    def test_caps_one_item_per_source(self) -> None:
        result = select_items(
            [ranked("Dan Luu", 1), ranked("Dan Luu", 2, uid="second")],
            POLICY,
            SOURCES,
        )
        self.assertEqual(len(result.items), 1)
        self.assertIn("cuota de fuente agotada", result.rejected[0][1])

    def test_caps_two_items_per_group(self) -> None:
        result = select_items(
            [
                ranked("OpenAI Blog", 1),
                ranked("Anthropic Blog", 2),
                ranked("Google DeepMind", 3),
                ranked("Dan Luu", 4),
            ],
            POLICY,
            SOURCES,
        )
        self.assertEqual(names(result), ["OpenAI Blog", "Anthropic Blog", "Dan Luu"])
        self.assertTrue(any("cuota de grupo agotada (labs" in reason for _, reason in result.rejected))

    def test_reserved_slots_survive_a_flood_of_vendor_items(self) -> None:
        policy = EditorialPolicy(
            min_importance=40,
            max_items=4,
            max_per_source=1,
            max_per_group=4,
            group_limits=(),
            reserved_topics=frozenset({"opinion", "security"}),
            reserved_slots=2,
            relax_floor_if_empty=True,
            relaxed_max_items=3,
            require_why=True,
        )
        # Cuatro items de vendor con mejor rank que los dos reservados: sin
        # reserva se llevarian toda la edicion.
        candidates = [
            ranked("OpenAI Blog", 1, importance=95),
            ranked("Anthropic Blog", 2, importance=90),
            ranked("Vercel Blog", 3, importance=85),
            ranked("Cloudflare Blog", 4, importance=80),
            ranked("Dan Luu", 5, importance=60),
            ranked("Krebs", 6, importance=55),
        ]
        result = select_items(candidates, policy, SOURCES)
        self.assertEqual(names(result), ["OpenAI Blog", "Anthropic Blog", "Dan Luu", "Krebs"])

    def test_unused_reserved_slots_are_backfilled(self) -> None:
        policy = EditorialPolicy(
            min_importance=40,
            max_items=3,
            max_per_source=1,
            max_per_group=3,
            group_limits=(),
            reserved_topics=frozenset({"opinion", "security"}),
            reserved_slots=2,
            relax_floor_if_empty=True,
            relaxed_max_items=3,
            require_why=True,
        )
        candidates = [
            ranked("OpenAI Blog", 1),
            ranked("Vercel Blog", 2),
            ranked("Cloudflare Blog", 3),
        ]
        result = select_items(candidates, policy, SOURCES)
        self.assertEqual(len(result.items), 3)

    def test_relaxes_the_floor_instead_of_shipping_nothing(self) -> None:
        result = select_items(
            [ranked("Dan Luu", 1, importance=10), ranked("Vercel Blog", 2, importance=5)],
            POLICY,
            SOURCES,
        )
        self.assertEqual(len(result.items), 2)

    def test_relaxing_the_floor_still_honours_ai_discards(self) -> None:
        result = select_items(
            [ranked("Vercel Blog", 1, importance=10, discarded=True), ranked("Dan Luu", 2, importance=10)],
            POLICY,
            SOURCES,
        )
        self.assertEqual(names(result), ["Dan Luu"])

    def test_empty_edition_when_everything_was_discarded(self) -> None:
        result = select_items(
            [ranked("Vercel Blog", 1, discarded=True), ranked("OpenAI Blog", 2, discarded=True)],
            POLICY,
            SOURCES,
        )
        self.assertEqual(result.items, [])
        self.assertEqual(len(result.rejected), 2)

    def test_ranks_are_renumbered_from_one(self) -> None:
        result = select_items(
            [ranked("Vercel Blog", 1, importance=20), ranked("Dan Luu", 7), ranked("Krebs", 9)],
            POLICY,
            SOURCES,
        )
        self.assertEqual([item.rank for item in result.items], [1, 2])

    def test_per_group_override_beats_the_global_cap(self) -> None:
        from dataclasses import replace as dc_replace

        policy = dc_replace(POLICY, group_limits=(("labs", 3),))
        candidates = [
            ranked("OpenAI Blog", 1),
            ranked("Anthropic Blog", 2),
            ranked("Google DeepMind", 3),
            ranked("Vercel Blog", 4),
            ranked("Cloudflare Blog", 5),
        ]
        result = select_items(candidates, policy, SOURCES)
        kept = names(result)
        labs = {"OpenAI Blog", "Anthropic Blog", "Google DeepMind"}
        # labs sube a 3; cloud sigue en el tope global de 2
        self.assertEqual(len([name for name in kept if name in labs]), 3)
        self.assertEqual(len([name for name in kept if name not in labs]), 2)
        self.assertEqual(result.rejected, [])

    def test_unknown_source_is_its_own_group(self) -> None:
        result = select_items([ranked("Mystery Feed", 1), ranked("Dan Luu", 2)], POLICY, SOURCES)
        self.assertEqual(len(result.items), 2)


if __name__ == "__main__":
    unittest.main()


class HeuristicImportanceTest(unittest.TestCase):
    def test_floor_is_skipped_when_importances_are_heuristic(self) -> None:
        # Puntuaciones tipicas del ranking heuristico: otra escala, no comparables
        # con el 1-100 del ranker.
        candidates = [ranked("Dan Luu", 1, importance=24), ranked("Krebs", 2, importance=16)]
        with_floor = select_items(candidates, POLICY, SOURCES)
        without_floor = select_items(candidates, POLICY, SOURCES, apply_importance_floor=False)
        self.assertEqual(len(with_floor.items), 2)  # el relax evita la edicion vacia
        self.assertEqual(len(without_floor.items), 2)
        self.assertEqual(without_floor.rejected, [])

    def test_quotas_still_apply_without_the_floor(self) -> None:
        candidates = [
            ranked("OpenAI Blog", 1, importance=20),
            ranked("Anthropic Blog", 2, importance=18),
            ranked("Google DeepMind", 3, importance=16),
        ]
        result = select_items(candidates, POLICY, SOURCES, apply_importance_floor=False)
        self.assertEqual(len(result.items), 2)
        self.assertIn("cuota de grupo agotada (labs", result.rejected[0][1])


class RequireWhyTest(unittest.TestCase):
    def test_item_without_why_is_dropped(self) -> None:
        result = select_items(
            [ranked("OpenAI Blog", 1, why=""), ranked("Dan Luu", 2)],
            POLICY,
            SOURCES,
        )
        self.assertEqual(names(result), ["Dan Luu"])
        self.assertIn("no supo decir por que importa", result.rejected[0][1])

    def test_blank_why_counts_as_empty(self) -> None:
        result = select_items([ranked("Dan Luu", 1, why="   ")], POLICY, SOURCES)
        # cede la regla antes que dejar la edicion vacia
        self.assertEqual(len(result.items), 1)

    def test_requirement_can_be_switched_off(self) -> None:
        from dataclasses import replace as dc_replace

        policy = dc_replace(POLICY, require_why=False)
        result = select_items([ranked("OpenAI Blog", 1, why=""), ranked("Dan Luu", 2)], policy, SOURCES)
        self.assertEqual(len(result.items), 2)

    def test_why_requirement_survives_the_heuristic_path(self) -> None:
        # En modo degradado el 'why' lo rellena la heuristica, asi que no vacia nada.
        result = select_items(
            [ranked("Dan Luu", 1, importance=20, why="Seleccion automatica.")],
            POLICY,
            SOURCES,
            apply_importance_floor=False,
        )
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.rejected, [])
