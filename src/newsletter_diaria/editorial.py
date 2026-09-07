from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, replace

from newsletter_diaria.models import EditorialPolicy, RankedItem, Source

logger = logging.getLogger("newsletter_diaria")

# Valor practico para "sin limite" cuando max_items viene a 0.
UNLIMITED = 10_000

# Rechazo temporal: el articulo no cabe entre los huecos generales, pero puede
# entrar en la segunda pasada si la reserva editorial se queda sin cubrir.
DEFERRED = "reserva editorial"


@dataclass(frozen=True)
class SelectionResult:
    items: list[RankedItem]
    rejected: list[tuple[RankedItem, str]]


def select_items(
    items: list[RankedItem],
    policy: EditorialPolicy,
    sources_by_name: dict[str, Source],
    apply_importance_floor: bool = True,
) -> SelectionResult:
    """Aplica la politica editorial: descartes de la IA, umbral de importancia,
    cuota por fuente y por grupo, reserva de huecos y limite duro.

    El orden importa: primero se retira lo que la IA marco como ruido, luego lo
    que no llega al umbral, y solo despues se reparten los huecos."""
    ordered = sorted(items, key=lambda ranked: (ranked.rank, -ranked.importance))
    rejected: list[tuple[RankedItem, str]] = []
    candidates: list[RankedItem] = []
    for ranked in ordered:
        if ranked.discarded:
            rejected.append((ranked, discard_label(ranked)))
        else:
            candidates.append(ranked)

    max_items = policy.max_items if policy.max_items > 0 else UNLIMITED
    if apply_importance_floor:
        eligible = [ranked for ranked in candidates if ranked.importance >= policy.min_importance]
        below_floor = [ranked for ranked in candidates if ranked.importance < policy.min_importance]
    else:
        # Sin ranker de IA la importancia es un score heuristico en otra escala:
        # compararlo con el umbral descartaria casi todo por artefacto.
        logger.info("Importance floor skipped: importances come from the heuristic ranking")
        eligible = candidates
        below_floor = []

    kept, quota_rejected = apply_quotas(eligible, policy, sources_by_name, max_items)
    rejected.extend(
        (ranked, f"importancia {ranked.importance} por debajo del umbral {policy.min_importance}")
        for ranked in below_floor
    )
    rejected.extend(quota_rejected)

    # Red de seguridad: el umbral lo fija un modelo barato sobre titulares, asi
    # que si deja la edicion vacia lo relajamos. Los descartes explicitos de la
    # IA y las cuotas siguen aplicando: solo cede el umbral numerico.
    if not kept and policy.relax_floor_if_empty and below_floor:
        limit = min(policy.relaxed_max_items, max_items)
        logger.warning(
            "No item cleared the importance floor (%d); relaxing it for up to %d items",
            policy.min_importance,
            limit,
        )
        kept, quota_rejected = apply_quotas(candidates, policy, sources_by_name, limit)
        rejected = [(ranked, discard_label(ranked)) for ranked in ordered if ranked.discarded]
        rejected.extend(quota_rejected)

    renumbered = [replace(ranked, rank=index) for index, ranked in enumerate(kept, start=1)]
    return SelectionResult(items=renumbered, rejected=rejected)


def apply_quotas(
    candidates: list[RankedItem],
    policy: EditorialPolicy,
    sources_by_name: dict[str, Source],
    max_items: int,
) -> tuple[list[RankedItem], list[tuple[RankedItem, str]]]:
    kept: list[RankedItem] = []
    rejected: list[tuple[RankedItem, str]] = []
    per_source: Counter[str] = Counter()
    per_group: Counter[str] = Counter()
    group_limits = dict(policy.group_limits)
    general_cap = max(0, max_items - policy.reserved_slots)
    general_used = 0
    deferred: list[RankedItem] = []

    def try_accept(ranked: RankedItem, honour_reserve: bool) -> str | None:
        """Devuelve el motivo de rechazo, o None si el articulo entra."""
        nonlocal general_used
        if len(kept) >= max_items:
            return "fuera del limite de la edicion"
        source_name = ranked.item.source
        group = group_of(ranked, sources_by_name)
        if 0 < policy.max_per_source <= per_source[source_name]:
            return f"cuota de fuente agotada ({source_name})"
        group_cap = group_limits.get(group, policy.max_per_group)
        if 0 < group_cap <= per_group[group]:
            return f"cuota de grupo agotada ({group}, tope {group_cap})"
        reserved = topic_of(ranked, sources_by_name) in policy.reserved_topics
        if honour_reserve and not reserved and general_used >= general_cap:
            return DEFERRED
        kept.append(ranked)
        per_source[source_name] += 1
        per_group[group] += 1
        if not reserved:
            general_used += 1
        return None

    for ranked in candidates:
        reason = try_accept(ranked, honour_reserve=True)
        if reason == DEFERRED:
            # Aun puede entrar si la reserva se queda sin cubrir.
            deferred.append(ranked)
        elif reason:
            rejected.append((ranked, reason))

    # Segunda pasada: no desperdiciamos los huecos reservados si no hay
    # candidatos de los temas reservados que los ocupen.
    for ranked in deferred:
        reason = try_accept(ranked, honour_reserve=False)
        if reason:
            rejected.append((ranked, reason))

    kept.sort(key=lambda ranked: (ranked.rank, -ranked.importance))
    return kept, rejected


def group_of(ranked: RankedItem, sources_by_name: dict[str, Source]) -> str:
    source = sources_by_name.get(ranked.item.source)
    if source and source.group:
        return source.group
    return ranked.item.source


def topic_of(ranked: RankedItem, sources_by_name: dict[str, Source]) -> str:
    source = sources_by_name.get(ranked.item.source)
    return source.topic if source else "general"


def discard_label(ranked: RankedItem) -> str:
    return f"descartado por la IA: {ranked.discard_reason or 'sin motivo'}"


def log_rejections(rejected: list[tuple[RankedItem, str]]) -> None:
    if not rejected:
        return
    logger.info("Editorial policy dropped %d candidate(s)", len(rejected))
    for ranked, reason in rejected:
        logger.info("  - [%s] %s :: %s", ranked.item.source, ranked.item.title[:70], reason)
