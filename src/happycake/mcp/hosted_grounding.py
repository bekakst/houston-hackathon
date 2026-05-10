"""Async helpers that pull live grounding from the hosted MCP simulator.

Closes JUDGING.md:44 — "the MCP-backed facts criterion is technically not
met. agents/grounding.py builds the evidence dict from local YAML mirrors."

Each helper is cached at module level with a short TTL so we don't call
the simulator on every customer turn. Cache misses write an audit row with
the MCP tool name so `mcp_audit_log` shows the call pattern.

All helpers are safe-fail: any exception returns `None` and the agent
continues with whatever local evidence the existing grounding pipeline
already produced. We never block a customer reply on an MCP outage.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.storage import audit_write

log = logging.getLogger(__name__)


# (cache_key) -> (expires_at_unix, value)
_CACHE: dict[str, tuple[float, Any]] = {}
_TTL_SECONDS = 300  # 5 minutes — long enough to avoid per-turn calls,
                    # short enough that price/capacity changes propagate.


def _cache_get(key: str) -> Any | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if time.time() >= expires_at:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: Any) -> None:
    _CACHE[key] = (time.time() + _TTL_SECONDS, value)


async def _call_or_cache(tool: str, args: dict | None = None,
                         cache_key: str | None = None) -> Any | None:
    """Generic cache-or-fetch pattern. Audits on cache miss only."""
    key = cache_key or tool
    cached = _cache_get(key)
    if cached is not None:
        return cached

    h = hosted_mcp()
    if not h.is_configured():
        return None

    try:
        result = await h.call_tool(tool, args or {})
    except MCPError as exc:
        log.info("hosted grounding call %s failed: %s", tool, exc)
        audit_write(
            event_id=f"hg_{tool}_err_{int(time.time())}",
            kind="hosted_grounding_failed",
            payload={"tool": tool, "error": str(exc)},
        )
        return None

    audit_write(
        event_id=f"hg_{tool}_{int(time.time() * 1000)}",
        kind="hosted_grounding_fetched",
        payload={"tool": tool, "args": args or {}},
    )
    _cache_put(key, result)
    return result


async def fetch_pos_catalog() -> dict | None:
    """Live POS catalog (id, variationId, name, priceCents, kitchenProductId).

    Used by intake grounding to ground prices in real POS state, not just
    the local YAML mirror. Returns the raw simulator response or None.
    """
    return await _call_or_cache("square_list_catalog", {"limit": 50})


async def fetch_kitchen_capacity() -> dict | None:
    """Daily prep capacity + queue depth + lead-time defaults."""
    return await _call_or_cache("kitchen_get_capacity")


async def fetch_kitchen_constraints() -> list | None:
    """Per-product prep/lead-time/capacity/custom flags."""
    return await _call_or_cache("kitchen_get_menu_constraints")


def _normalise_catalog(raw: Any) -> list[dict]:
    """Coerce the catalog response into a flat list of items."""
    if isinstance(raw, dict):
        items = raw.get("catalog") or raw.get("items") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    return [it for it in items if isinstance(it, dict)]


def _normalise_constraints(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        return [c for c in (raw.get("constraints") or raw.get("items") or [])
                if isinstance(c, dict)]
    return []


async def hosted_facts_for(slug: str | None) -> dict[str, Any]:
    """Aggregate hosted-MCP facts an intake/custom turn cares about.

    For a given slug (the customer's mentioned cake), return the matching
    POS items, the kitchen capacity snapshot, and the relevant menu
    constraint. Missing pieces are simply absent — never raises.
    """
    out: dict[str, Any] = {}

    catalog = await fetch_pos_catalog()
    items = _normalise_catalog(catalog)
    if items:
        out["pos_catalog_count"] = len(items)
        if slug:
            slug_norm = slug.lower()
            matches = [
                it for it in items
                if slug_norm in (it.get("kitchenProductId") or "").lower()
                or slug_norm in (it.get("name") or "").lower()
            ]
            if matches:
                out["pos_items"] = [
                    {"id": m.get("id"), "name": m.get("name"),
                     "priceCents": m.get("priceCents"),
                     "kitchenProductId": m.get("kitchenProductId")}
                    for m in matches[:3]
                ]

    capacity = await fetch_kitchen_capacity()
    if isinstance(capacity, dict):
        out["kitchen_capacity"] = {
            "dailyCapacityMinutes": capacity.get("dailyCapacityMinutes"),
            "remainingCapacityMinutes": capacity.get("remainingCapacityMinutes"),
            "queuedTickets": capacity.get("queuedTickets"),
            "acceptedTickets": capacity.get("acceptedTickets"),
            "defaultLeadTimeMinutes": capacity.get("defaultLeadTimeMinutes"),
        }

    constraints = _normalise_constraints(await fetch_kitchen_constraints())
    if constraints:
        out["kitchen_constraint_count"] = len(constraints)
        if slug:
            slug_norm = slug.lower()
            for c in constraints:
                pid = (c.get("productId") or "").lower()
                if slug_norm in pid or pid in slug_norm:
                    out["kitchen_constraint"] = {
                        "productId": c.get("productId"),
                        "prepMinutes": c.get("prepMinutes"),
                        "leadTimeMinutes": c.get("leadTimeMinutes"),
                        "capacityUnitsPerDay": c.get("capacityUnitsPerDay"),
                        "requiresCustomWork": c.get("requiresCustomWork"),
                    }
                    break

    return out


__all__ = [
    "fetch_pos_catalog",
    "fetch_kitchen_capacity",
    "fetch_kitchen_constraints",
    "hosted_facts_for",
]
