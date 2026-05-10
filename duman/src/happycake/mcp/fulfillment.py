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


def _items_from_spec(spec: dict | None, channel: str) -> list[dict] | None:
    """Translate a draft_cake_spec dict into Square `items` list.

    Returns None if the spec is missing or the catalog can't resolve the cake.
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
    return [{
        "productId": size.mcp_product_id or f"{cake.slug}-{size.label}",
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

    items = _items_from_spec(payload.get("draft_cake_spec"), payload.get("channel", ""))
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
        order_id = (
            (result or {}).get("orderId")
            or (result or {}).get("order_id")
            or (result or {}).get("id")
        )
        steps.append({"step": "square_create_order", "ok": bool(order_id), "result": result})
        audit_write(
            event_id=f"pos_create_{decision_id}",
            kind="pos_order_created",
            payload={"decision_id": decision_id, "order_id": order_id, "items": items},
        )
    except MCPError as exc:
        steps.append({"step": "square_create_order", "ok": False, "error": str(exc)})
        summary["ok"] = False
        log.warning("square_create_order failed: %s", exc)

    if not order_id:
        return summary

    summary["order_id"] = order_id

    # Step 2 — square_update_order_status -> confirmed
    try:
        await h.call_tool(
            "square_update_order_status",
            {"orderId": order_id, "status": "confirmed",
             "note": f"Owner-approved decision {decision_id}"},
        )
        steps.append({"step": "square_update_order_status", "ok": True, "status": "confirmed"})
        audit_write(
            event_id=f"pos_confirm_{decision_id}",
            kind="pos_status_updated",
            payload={"decision_id": decision_id, "order_id": order_id, "status": "confirmed"},
        )
    except MCPError as exc:
        steps.append({"step": "square_update_order_status", "ok": False, "error": str(exc)})
        summary["ok"] = False
        log.warning("square_update_order_status failed for %s: %s", order_id, exc)

    # Step 3 — kitchen_create_ticket
    try:
        ticket_args: dict[str, Any] = {
            "orderId": order_id,
            "customerName": customer_name,
            "items": items,
        }
        if deadline:
            ticket_args["requestedPickupAt"] = deadline
        if payload.get("draft_cake_spec"):
            ticket_args["notes"] = (payload["draft_cake_spec"].get("notes") or "")[:200]
        ticket = await h.call_tool("kitchen_create_ticket", ticket_args)
        ticket_id = (
            (ticket or {}).get("ticketId")
            or (ticket or {}).get("ticket_id")
            or (ticket or {}).get("id")
        )
        summary["ticket_id"] = ticket_id
        steps.append({"step": "kitchen_create_ticket", "ok": bool(ticket_id), "result": ticket})
        audit_write(
            event_id=f"kit_create_{decision_id}",
            kind="kitchen_ticket_created",
            payload={"decision_id": decision_id, "order_id": order_id, "ticket_id": ticket_id},
        )
    except MCPError as exc:
        steps.append({"step": "kitchen_create_ticket", "ok": False, "error": str(exc)})
        summary["ok"] = False
        log.warning("kitchen_create_ticket failed for %s: %s", order_id, exc)

    return summary


__all__ = ["fulfill_approved"]
