"""Grounding helpers — pre-fetch MCP/local facts based on the customer turn so
the specialist agent's prompt has all the data it needs to answer without
inventing anything.

Heuristics here are intentionally simple (substring match against catalog
slugs / aliases). The LLM does the actual semantic work; we just build the
evidence dictionary.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from happycake.mcp import catalog as catalog_mcp
from happycake.mcp import inventory as inventory_mcp
from happycake.mcp import kitchen as kitchen_mcp
from happycake.mcp import pricing as pricing_mcp
from happycake.mcp.local_data import load_policies

log = logging.getLogger(__name__)

# Map slug + a few common aliases the customer might type.
_CAKE_ALIASES = {
    "honey":          ["honey", "medovik", "medovuk"],
    "napoleon":       ["napoleon", "napolean"],
    "milk-maiden":    ["milk maiden", "milk-maiden", "milkmaiden", "molochnaya devochka"],
    "pistachio-roll": ["pistachio roll", "pistachio-roll", "pistachio"],
    "tiramisu":       ["tiramisu"],
    "cloud":          ["cloud", "berry cloud"],
    "carrot":         ["carrot"],
    "red-velvet":     ["red velvet", "red-velvet"],
    "custom":         ["custom", "design", "wedding cake", "tiered"],
}

_SIZE_TOKENS = {
    "slice": "slice",
    "whole": "whole",
    "small": "small",
    "medium": "medium",
    "large": "large",
}

_FULFILLMENT_TOKENS = {
    "pickup": "pickup",
    "pick up": "pickup",
    "pick-up": "pickup",
    "delivery": "delivery",
    "deliver": "delivery",
    "ship":  "delivery",
}


def _detect_cake_slug(text: str) -> str | None:
    lower = text.lower()
    for slug, aliases in _CAKE_ALIASES.items():
        for a in aliases:
            if a in lower:
                return slug
    return None


def _detect_cake_slugs_all(text: str) -> list[str]:
    """All catalog cakes mentioned, in catalog order (deduped)."""
    lower = text.lower()
    found: list[str] = []
    for slug, aliases in _CAKE_ALIASES.items():
        if slug == "custom":
            continue
        for a in aliases:
            if a in lower and slug not in found:
                found.append(slug)
                break
    return found


_PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")


def _detect_phone(text: str) -> str | None:
    m = _PHONE_RE.search(text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return digits if len(digits) >= 7 else None


def _detect_size(text: str) -> str | None:
    lower = text.lower()
    for token, label in _SIZE_TOKENS.items():
        if re.search(rf"\b{re.escape(token)}\b", lower):
            return label
    return None


def _detect_fulfillment(text: str) -> str | None:
    lower = text.lower()
    for token, label in _FULFILLMENT_TOKENS.items():
        if token in lower:
            return label
    return None


def _detect_serves(text: str) -> int | None:
    """Look for 'for N people' / 'N guests' / 'N kids'."""
    m = re.search(r"\bfor\s+(\d+)\s+(people|guests|kids|children|adults)\b",
                  text, flags=re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d+)\s+(people|guests|kids|children|adults)\b", text, flags=re.I)
    if m:
        return int(m.group(1))
    return None


def _detect_order_id(text: str) -> str | None:
    m = re.search(r"\bord_[a-z0-9_]+\b", text, flags=re.I)
    if m:
        return m.group(0)
    return None


def _ground_intake(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"detected": {}}
    slug = _detect_cake_slug(text)
    all_slugs = _detect_cake_slugs_all(text)
    size = _detect_size(text)
    fulfillment = _detect_fulfillment(text)
    serves = _detect_serves(text)
    phone = _detect_phone(text)

    out["detected"] = {
        "cake_slug": slug,
        "cake_slugs_all": all_slugs,
        "size_label": size,
        "fulfillment": fulfillment,
        "serves": serves,
        "phone_hint": phone,
    }

    # Earliest ready timestamp for whatever cakes were detected. Baking is
    # parallel, so it's max(lead_time) — not sum. Surface as both hours and
    # an absolute UTC ISO string so the agent can quote a real time.
    lead_basis = all_slugs or ([slug] if slug else [])
    if lead_basis:
        lead_h = catalog_mcp.min_lead_time_hours(lead_basis)
        now_utc = datetime.now(tz=timezone.utc)
        earliest = now_utc + timedelta(hours=lead_h)
        out["lead_time"] = {
            "min_hours": lead_h,
            "earliest_ready_at_utc": earliest.isoformat(),
            "now_utc": now_utc.isoformat(),
            "note": "Parallel baking: time = max lead-time across items, not sum.",
        }

    if slug:
        cake = catalog_mcp.get(slug)
        if cake:
            out["catalog_cake"] = cake.model_dump(mode="json")
            if size:
                quote = pricing_mcp.quote(slug, size, fulfillment=fulfillment or "pickup")
                out["quote"] = quote
            today = datetime.now(tz=timezone.utc).date()
            out["inventory_today"] = inventory_mcp.available(slug, today)
        else:
            out["catalog_cake_not_found"] = slug

    if serves:
        out["catalog_by_serves"] = [
            c.model_dump(mode="json") for c in catalog_mcp.search_by_serves(serves)
        ][:4]

    # Always surface a compact catalog overview so the intake specialist can
    # answer greetings and "what do you have" questions without re-prompting.
    try:
        all_cakes = catalog_mcp.list_all()
    except Exception:  # noqa: BLE001
        all_cakes = []
    out["catalog_overview"] = [
        {
            "slug": c.slug,
            "name": c.name,
            "short_description": c.short_description,
            "serves_min": c.serves_min,
            "serves_max": c.serves_max,
            "available_daily": c.available_daily,
        }
        for c in all_cakes
    ]

    return out


def _ground_custom(text: str, partial_spec: dict | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"detected": {}}
    slug = _detect_cake_slug(text) or (partial_spec or {}).get("base_cake_slug")
    serves = _detect_serves(text)
    fulfillment = _detect_fulfillment(text)
    out["detected"] = {
        "cake_slug": slug,
        "serves": serves,
        "fulfillment": fulfillment,
    }
    out["kitchen_calendar_summary"] = kitchen_mcp.calendar_summary(days=10)
    if slug:
        cake = catalog_mcp.get(slug)
        if cake:
            out["catalog_cake"] = cake.model_dump(mode="json")
    return out


def _ground_care(text: str, *, verified: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"detected": {}, "verified": verified}
    order_id = _detect_order_id(text)
    out["detected"] = {"order_id": order_id}
    if order_id:
        from happycake.mcp import orders as orders_mcp
        record = orders_mcp.get(order_id)
        out["order"] = record  # may be None; care agent handles "not found"
    out["policies"] = {
        "refund": load_policies()["refund"],
        "allergens": load_policies()["allergens"],
        "delivery_zones": load_policies()["delivery_zones"],
    }
    return out


def _common_evidence() -> dict[str, Any]:
    return {
        "policies": {
            "hours": load_policies()["hours"],
            "pickup": load_policies()["pickup"],
            "delivery_zones": load_policies()["delivery_zones"],
            "custom_cake": load_policies()["custom_cake"],
        },
        "now_utc": datetime.now(tz=timezone.utc).isoformat(),
    }


def ground_for_intent(intent: str, text: str, *,
                      partial_spec: dict | None = None,
                      verified: bool = False) -> dict[str, Any]:
    """Pre-fetch MCP/local facts for the specialist agent's prompt envelope."""
    base = _common_evidence()
    if intent == "intake":
        base["intake"] = _ground_intake(text)
    elif intent == "custom":
        base["custom"] = _ground_custom(text, partial_spec)
    elif intent == "care":
        base["care"] = _ground_care(text, verified=verified)
    return base
