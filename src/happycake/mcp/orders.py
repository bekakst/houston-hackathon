"""Orders MCP client — local stub. In production this writes to Square/POS via
the hosted MCP. Locally we persist drafts to the events/decisions tables and
generate stable order ids that survive replay.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.schemas import Order, OrderStatus
from happycake.storage import audit_write, connect, now_iso

log = logging.getLogger(__name__)


def _new_order_id() -> str:
    return f"ord_{secrets.token_hex(4)}"


def _ensure_table() -> None:
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS orders ("
            "  order_id TEXT PRIMARY KEY,"
            "  payload TEXT NOT NULL,"
            "  status TEXT NOT NULL,"
            "  created_at TEXT NOT NULL"
            ")"
        )
        conn.commit()


def draft(order: Order) -> dict:
    """Persist a draft order. Idempotent on order_id."""
    _ensure_table()
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO orders (order_id, payload, status, created_at) "
            "VALUES (?, ?, ?, ?)",
            (order.order_id, order.model_dump_json(), order.status.value, now_iso()),
        )
        conn.commit()
    audit_write(
        event_id=f"ord_draft_{order.order_id}",
        kind="order_drafted",
        payload={"order_id": order.order_id, "channel": order.channel.value},
    )
    return {"ok": True, "order_id": order.order_id}


def get(order_id: str) -> dict | None:
    _ensure_table()
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, status FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    if not row:
        return None
    payload = json.loads(row["payload"])
    payload["status"] = row["status"]
    return payload


def set_status(order_id: str, status: OrderStatus) -> bool:
    _ensure_table()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE orders SET status = ? WHERE order_id = ?",
            (status.value, order_id),
        )
        conn.commit()
    return cur.rowcount > 0


def make_id() -> str:
    return _new_order_id()


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


async def fetch_recent_from_square(*, limit: int = 20,
                                   match_order_id: str | None = None,
                                   match_phone_last4: str | None = None,
                                   ) -> dict[str, Any]:
    """Pull recent orders from the hosted Square simulator and try to match.

    The local `ord_*` ids and the simulator's `sq_order_*` ids are independent
    so we match best-effort: order_id substring in id/customerNote, or
    phone-last-4 inside customerNote (fulfillment puts `phone:<digits>` there).

    Returns:
        {ok, source, recent: [...], matched: {...}|None, mode: "live"|"unconfigured"|"error"}

    Always returns; never raises. The care prompt sees the result via evidence
    and decides what to share with the customer.
    """
    h = hosted_mcp()
    if not h.is_configured():
        return {"ok": True, "source": "square_recent_orders", "recent": [],
                "matched": None, "mode": "unconfigured"}

    try:
        result = await h.call_tool("square_recent_orders", {"limit": limit})
    except MCPError as exc:
        log.info("square_recent_orders unavailable: %s", exc)
        audit_write(
            event_id=f"care_sq_err_{secrets.token_hex(4)}",
            kind="care_square_lookup_failed",
            payload={"error": str(exc), "match_order_id": match_order_id,
                     "match_phone_last4": match_phone_last4},
        )
        return {"ok": False, "source": "square_recent_orders", "recent": [],
                "matched": None, "mode": "error", "error": str(exc)}

    if isinstance(result, dict):
        orders = result.get("orders") or result.get("items") or []
    elif isinstance(result, list):
        orders = result
    else:
        orders = []

    matched: dict | None = None
    if orders and (match_order_id or match_phone_last4):
        for o in orders:
            if not isinstance(o, dict):
                continue
            haystack = " ".join([
                str(o.get("id") or ""),
                str(o.get("customerNote") or ""),
                str(o.get("customerName") or ""),
            ])
            if match_order_id and match_order_id in haystack:
                matched = o
                break
            if match_phone_last4 and match_phone_last4 in haystack:
                matched = o
                break

    audit_write(
        event_id=f"care_sq_lookup_{secrets.token_hex(4)}",
        kind="care_square_lookup",
        payload={"recent_count": len(orders),
                 "match_order_id": match_order_id,
                 "match_phone_last4": match_phone_last4,
                 "matched_id": (matched or {}).get("id")},
    )

    # Trim each order to the fields the prompt actually needs. The full
    # customerNote can be 280 chars; cap it so the envelope stays small.
    def _trim(o: dict) -> dict:
        return {
            "id": o.get("id"),
            "status": o.get("status"),
            "createdAt": o.get("createdAt"),
            "totalCents": o.get("totalCents"),
            "customerName": o.get("customerName"),
            "customerNote": (o.get("customerNote") or "")[:200],
            "items": [
                {"name": it.get("name"), "quantity": it.get("quantity")}
                for it in (o.get("items") or [])
                if isinstance(it, dict)
            ],
        }

    return {
        "ok": True,
        "source": "square_recent_orders",
        "mode": "live",
        "recent": [_trim(o) for o in orders if isinstance(o, dict)][:5],
        "matched": _trim(matched) if matched else None,
    }
