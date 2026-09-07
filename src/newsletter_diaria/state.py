from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from newsletter_diaria.models import Item
from newsletter_diaria.utils import parse_datetime

logger = logging.getLogger("newsletter_diaria")

# Parametros de query que solo sirven para analitica: dos enlaces que solo
# difieren en ellos apuntan al mismo articulo.
TRACKING_PREFIXES = ("utm_", "mc_", "mtm_", "pk_")
TRACKING_PARAMS = {"ref", "source", "fbclid", "gclid", "mkt_tok", "cmp", "at_medium"}


def normalize_link(link: str) -> str:
    """Clave estable de articulo: sin esquema, sin www, sin barra final y sin
    parametros de tracking. Asi el mismo post servido por dos feeds distintos
    (p. ej. GitHub Blog y GitHub Engineering) colapsa en una sola entrada."""
    raw = (link or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw if "//" in raw else f"//{raw}")
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    query = "&".join(sorted(piece for piece in parts.query.split("&") if piece and not is_tracking_param(piece)))
    return urlunsplit(("", host, path, query, ""))


def is_tracking_param(piece: str) -> bool:
    key = piece.split("=", 1)[0].lower()
    return key.startswith(TRACKING_PREFIXES) or key in TRACKING_PARAMS


def item_key(item: Item) -> str:
    """Identidad del articulo entre ejecuciones. El uid incluye el nombre de la
    fuente, asi que no sirve para detectar el mismo articulo en dos feeds."""
    return normalize_link(item.link) or item.uid


def load_seen(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("Could not read the seen store %s (%s); starting from scratch", path, exc)
        return {}
    entries = data.get("items") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {str(key): str(value) for key, value in entries.items()}


def filter_unseen(items: Iterable[Item], seen: dict[str, str]) -> list[Item]:
    return [item for item in items if item_key(item) not in seen]


def record_seen(path: Path, items: Iterable[Item], seen: dict[str, str], retention_days: int) -> None:
    """Anota los articulos ya enviados y poda los que quedan fuera de retencion,
    para que el fichero no crezca sin limite."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, retention_days))
    kept: dict[str, str] = {}
    for key, stamp in seen.items():
        moment = parse_datetime(stamp)
        if moment is None or moment >= cutoff:
            kept[key] = stamp
    stamp = now.isoformat()
    for item in items:
        kept[item_key(item)] = stamp
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": kept}, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Seen store updated: %d entries in %s", len(kept), path)
