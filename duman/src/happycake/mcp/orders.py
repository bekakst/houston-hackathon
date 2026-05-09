"""Orders MCP client — local stub. In production this writes to Square/POS via
the hosted MCP. Locally we persist drafts to the events/decisions tables and
generate stable order ids that survive replay.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from happycake.schemas import Order, OrderStatus
from happycake.storage import audit_write, connect, now_iso


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
