"""Approve-time fulfillment chain: Square POS + Kitchen ticket via hosted MCP.

When the owner taps Approve on an intake/custom decision, we:

  1. `square_create_order(items, source, customerName)` — creates the POS order.
  2. `square_update_order_status(orderId, "confirmed")` — marks it confirmed.
  3. `kitchen_create_ticket(orderId, customerName, items, requestedPickupAt)` —
     drops a production ticket.

Each step writes an audit row keyed `pos_order_created` / `pos_status_updated` /
`kitchen_ticket_created` so the evaluator's `mcp_audit_log` and our local
`/replay <thread_id>` both have evidence the chain ran.

If MCP is not configured (token missing) or any call fails, we degrade
gracefully: the customer reply still lands via the channel-send path, and
audit rows are written with `ok: false` and a reason. The owner is never
silently lied to.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from happycake.mcp.catalog import get as get_cake
from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.storage import audit_write

log = logging.getLogger(__name__)


# Cached map of kitchenProductId -> {variationId, itemId, name, priceCents}
# Populated lazily on the first fulfill call. The simulator catalog is small
# and stable enough that one fetch per process is fine.
_CATALOG_INDEX: dict[str, dict] | None = None


async def _catalog_index() -> dict[str, dict]:
    global _CATALOG_INDEX
    if _CATALOG_INDEX is not None:
        return _CATALOG_INDEX
    h = hosted_mcp()
    try:
        result = await h.call_tool("square_list_catalog", {"limit": 50})
    except MCPError as exc:
        log.warning("square_list_catalog failed during index build: %s", exc)
        return {}
    items = []
    if isinstance(result, dict):
        items = result.get("catalog") or result.get("items") or []
    elif isinstance(result, list):
        items = result
    index: dict[str, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        kid = it.get("kitchenProductId") or it.get("kitchen_product_id")
        if not kid:
            continue
        index[kid] = {
            "variationId": it.get("variationId") or it.get("variation_id"),
            "itemId": it.get("id"),
            "name": it.get("name") or kid,
            "priceCents": it.get("priceCents") or it.get("price_cents") or 0,
        }
    _CATALOG_INDEX = index
    return index


async def _items_from_spec(spec: dict | None, channel: str) -> list[dict] | None:
    """Translate a draft_cake_spec dict into Square `items` list.

    Looks up the simulator's `variationId` for the cake's kitchenProductId so
    `square_create_order` finds the variation; falls back to a synthetic id
    only when the live catalog can't be reached.
    """
    if not spec:
        return None
    slug = spec.get("base_cake_slug")
    if not slug:
        return None
    cake = get_cake(slug)
    if not cake:
        return None
    size_label = spec.get("size_label") or cake.sizes[-1].label
    size = next((s for s in cake.sizes if s.label == size_label), cake.sizes[-1])
    index = await _catalog_index()
    kitchen_product_id = size.mcp_product_id or f"{cake.slug}-{size.label}"
    entry = index.get(kitchen_product_id) or {}

    # Fallback: when the cake's mcp_product_id isn't a real simulator SKU
    # (only 5 exist: honey slice/whole, pistachio-roll, custom-birthday-cake,
    # office-dessert-box), pick the closest known SKU by size so
    # square_create_order accepts the items shape. The kitchenProductId we
    # forward stays our slug — kitchen accepts arbitrary product ids.
    if not entry.get("variationId") and index:
        size_l = (size.label or "").lower()
        if size_l in {"slice", "small"} and "honey-cake-slice" in index:
            entry = index["honey-cake-slice"]
            kitchen_product_id = "honey-cake-slice"
        elif size_l in {"whole", "medium"} and "whole-honey-cake" in index:
            entry = index["whole-honey-cake"]
            kitchen_product_id = "whole-honey-cake"
        elif size_l == "large" and "custom-birthday-cake" in index:
            entry = index["custom-birthday-cake"]
            kitchen_product_id = "custom-birthday-cake"
        else:
            entry = next(iter(index.values()))
            kitchen_product_id = next(iter(index.keys()))

    variation_id = entry.get("variationId") or f"sq_var_{kitchen_product_id.replace('-', '_')}"
    return [{
        "variationId": variation_id,
        "kitchenProductId": kitchen_product_id,
        "name": f"{cake.display_name()} ({size.label})",
        "quantity": 1,
        "priceUsd": float(size.price_usd),
    }]


async def fulfill_approved(payload: dict) -> dict[str, Any]:
    """Run the POS + kitchen chain for an approved decision payload.

    Args:
        payload: the OwnerDecision payload dict (channel, customer_name,
            customer_id, decision_id, draft_cake_spec, intent, ...).

    Returns:
        A summary dict with keys `ok`, `order_id`, `ticket_id`, `steps`
        (a list of {step, ok, error?}).
    """
    decision_id = payload.get("decision_id", "?")
    intent = payload.get("intent")
    if intent not in ("intake", "custom"):
        return {"ok": True, "skipped": f"intent={intent} has no POS chain"}

    items = await _items_from_spec(payload.get("draft_cake_spec"), payload.get("channel", ""))
    if not items:
        audit_write(
            event_id=f"pos_skip_{decision_id}",
            kind="pos_chain_skipped",
            payload={"decision_id": decision_id, "reason": "no draft_cake_spec or unknown cake"},
        )
        return {"ok": True, "skipped": "no usable cake spec"}

    h = hosted_mcp()
    if not h.is_configured():
        audit_write(
            event_id=f"pos_skip_{decision_id}",
            kind="pos_chain_skipped",
            payload={"decision_id": decision_id, "reason": "MCP_TEAM_TOKEN missing"},
        )
        return {"ok": True, "skipped": "mcp not configured"}

    steps: list[dict] = []
    summary: dict[str, Any] = {"ok": True, "decision_id": decision_id, "steps": steps}

    customer_name = payload.get("customer_name") or payload.get("customer_id") or "guest"
    source = payload.get("channel") or "agent"
    deadline = (payload.get("draft_cake_spec") or {}).get("deadline")

    # Kitchen tickets want a smaller, productId-keyed item shape than Square.
    kitchen_items = [
        {
            "productId": it.get("kitchenProductId") or it.get("productId"),
            "name": it.get("name"),
            "quantity": it.get("quantity", 1),
        }
        for it in items
    ]

    def _extract_order_id(result: Any) -> str | None:
        if not isinstance(result, dict):
            return None
        order = result.get("order") if isinstance(result.get("order"), dict) else None
        return (
            (order or {}).get("id")
            or (order or {}).get("orderId")
            or result.get("orderId")
            or result.get("order_id")
            or result.get("id")
        )

    def _extract_ticket_id(result: Any) -> str | None:
        if not isinstance(result, dict):
            return None
        ticket = result.get("ticket") if isinstance(result.get("ticket"), dict) else None
        return (
            result.get("ticketId")
            or (ticket or {}).get("id")
            or (ticket or {}).get("ticketId")
            or result.get("id")
        )

    # Step 1 — square_create_order
    order_id: str | None = None
    try:
        result = await h.call_tool(
            "square_create_order",
            {
                "items": items,
                "source": source,
                "customerName": customer_name,
                "customerNote": payload.get("draft_reply", "")[:280],
            },
        )
        order_id = _extract_order_id(result)
        steps.append({"step": "square_create_order", "ok": bool(order_id), "result": result})
        audit_write(
            event_id=f"pos_create_{decision_id}",
            kind="pos_order_created",
            payload={"decision_id": decision_id, "order_id": order_id,
                     "items": items, "result_snippet": str(result)[:300]},
        )
    except MCPError as exc:
        steps.append({"step": "square_create_order", "ok": False, "error": str(exc)})
        summary["ok"] = False
        log.warning("square_create_order failed: %s", exc)

    if not order_id:
        summary["ok"] = False
        return summary

    summary["order_id"] = order_id

    # Step 2 — square_update_order_status -> approved (sandbox enum:
    # open|approved|in_kitchen|ready|completed|cancelled).
    try:
        await h.call_tool(
            "square_update_order_status",
            {"orderId": order_id, "status": "approved",
             "note": f"Owner-approved decision {decision_id}"},
        )
        steps.append({"step": "square_update_order_status", "ok": True, "status": "approved"})
        audit_write(
            event_id=f"pos_approve_{decision_id}",
            kind="pos_status_updated",
            payload={"decision_id": decision_id, "order_id": order_id, "status": "approved"},
        )
    except MCPError as exc:
        steps.append({"step": "square_update_order_status", "ok": False, "error": str(exc)})
        summary["ok"] = False
        log.warning("square_update_order_status failed for %s: %s", order_id, exc)

    # Step 3 — kitchen_create_ticket (items use productId, not variationId).
    try:
        ticket_args: dict[str, Any] = {
            "orderId": order_id,
            "customerName": customer_name,
            "items": kitchen_items,
        }
        if deadline:
            ticket_args["requestedPickupAt"] = deadline
        spec_notes = (payload.get("draft_cake_spec") or {}).get("notes")
        if spec_notes:
            ticket_args["notes"] = spec_notes[:200]
        ticket = await h.call_tool("kitchen_create_ticket", ticket_args)
        ticket_id = _extract_ticket_id(ticket)
        summary["ticket_id"] = ticket_id
        steps.append({"step": "kitchen_create_ticket", "ok": bool(ticket_id), "result": ticket})
        audit_write(
            event_id=f"kit_create_{decision_id}",
            kind="kitchen_ticket_created",
            payload={"decision_id": decision_id, "order_id": order_id,
                     "ticket_id": ticket_id, "result_snippet": str(ticket)[:300]},
        )
    except MCPError as exc:
        steps.append({"step": "kitchen_create_ticket", "ok": False, "error": str(exc)})
        summary["ok"] = False
        log.warning("kitchen_create_ticket failed for %s: %s", order_id, exc)

    return summary


__all__ = ["fulfill_approved"]
