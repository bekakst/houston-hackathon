"""Customers MCP client — minimal local CRM-lite for the hackathon."""

from __future__ import annotations

import json
from typing import Any

from happycake.storage import connect, now_iso


def _ensure_table() -> None:
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS customers ("
            "  customer_id TEXT PRIMARY KEY,"
            "  channel TEXT,"
            "  name TEXT,"
            "  phone TEXT,"
            "  meta TEXT,"
            "  created_at TEXT,"
            "  last_seen_at TEXT"
            ")"
        )
        conn.commit()


def upsert(customer_id: str, *, channel: str, name: str | None = None,
           phone: str | None = None, meta: dict[str, Any] | None = None) -> None:
    _ensure_table()
    with connect() as conn:
        existing = conn.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE customers SET channel=?, name=COALESCE(?, name), "
                "phone=COALESCE(?, phone), meta=COALESCE(?, meta), last_seen_at=? "
                "WHERE customer_id=?",
                (channel, name, phone, json.dumps(meta) if meta else None,
                 now_iso(), customer_id),
            )
        else:
            conn.execute(
                "INSERT INTO customers (customer_id, channel, name, phone, meta, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (customer_id, channel, name, phone,
                 json.dumps(meta) if meta else None, now_iso(), now_iso()),
            )
        conn.commit()


def get(customer_id: str) -> dict | None:
    _ensure_table()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    if out.get("meta"):
        out["meta"] = json.loads(out["meta"])
    return out
