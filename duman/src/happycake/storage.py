"""SQLite-backed storage. Plain stdlib sqlite3 so a fresh clone has zero
extra setup. Three tables only — events (idempotency), decisions (owner
approval queue), audit (append-only log).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from happycake.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    external_id TEXT PRIMARY KEY,
    channel     TEXT NOT NULL,
    sender      TEXT NOT NULL,
    text        TEXT NOT NULL,
    received_at TEXT NOT NULL,
    response    TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    channel     TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    rejection_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status, created_at);

CREATE TABLE IF NOT EXISTS audit (
    event_id   TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_kind ON audit(kind, at);

CREATE TABLE IF NOT EXISTS reveal_orders (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id           TEXT NOT NULL UNIQUE,
    reveal_token       TEXT NOT NULL UNIQUE,
    orderer_name       TEXT NOT NULL,
    orderer_contact    TEXT NOT NULL,
    party_date         TEXT NOT NULL,
    pickup_or_delivery TEXT NOT NULL CHECK (pickup_or_delivery IN ('pickup','delivery')),
    delivery_address   TEXT,
    guest_count        INTEGER NOT NULL,
    cake_size_kg       REAL NOT NULL,
    decorations        TEXT,
    notes_to_baker     TEXT,
    state              TEXT NOT NULL CHECK (state IN ('pending_reveal','revealed','baking','ready','delivered','cancelled')) DEFAULT 'pending_reveal',
    gender             TEXT CHECK (gender IN ('boy','girl')),
    gender_set_at      TEXT,
    knower_ip_hash     TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reveal_orders_token ON reveal_orders(reveal_token);
CREATE INDEX IF NOT EXISTS idx_reveal_orders_state ON reveal_orders(state);
"""


def _db_path() -> Path:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        return Path(url.removeprefix("sqlite:///")).resolve()
    return Path("happycake.sqlite").resolve()


def init_db() -> None:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── Events / idempotency ────────────────────────────────────────────────────


def event_get(external_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM events WHERE external_id = ?",
            (external_id,),
        ).fetchone()
        return dict(row) if row else None


def event_insert(external_id: str, channel: str, sender: str, text: str,
                 received_at: str, response: str | None) -> bool:
    """Returns True if inserted, False if external_id already existed."""
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO events (external_id, channel, sender, text, received_at, response, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (external_id, channel, sender, text, received_at, response, now_iso()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def event_set_response(external_id: str, response: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE events SET response = ? WHERE external_id = ?",
            (response, external_id),
        )
        conn.commit()


# ── Decisions / owner approval queue ────────────────────────────────────────


def decision_insert(decision_id: str, kind: str, channel: str, customer_id: str,
                    payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO decisions (decision_id, kind, channel, customer_id, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (decision_id, kind, channel, customer_id, json.dumps(payload), now_iso()),
        )
        conn.commit()


def decision_get(decision_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d


def decision_list_pending(kind: str | None = None, limit: int = 25) -> list[dict]:
    sql = "SELECT * FROM decisions WHERE status = 'pending'"
    params: list = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY created_at ASC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict] = []
    for row in rows:
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def decision_customer_ids(kind: str, statuses: tuple[str, ...] = ("pending", "approved")) -> set[str]:
    """Return customer_ids for decisions of `kind` in any of `statuses`.

    Used by the GB review pipeline to dedupe across both pending and
    already-approved review replies.
    """
    placeholders = ",".join("?" * len(statuses))
    sql = f"SELECT customer_id FROM decisions WHERE kind = ? AND status IN ({placeholders})"
    params: list = [kind, *statuses]
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {r["customer_id"] for r in rows if r["customer_id"]}


def decision_set_status(decision_id: str, status: str,
                        rejection_reason: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE decisions SET status = ?, decided_at = ?, rejection_reason = ? "
            "WHERE decision_id = ?",
            (status, now_iso(), rejection_reason, decision_id),
        )
        conn.commit()


# ── Audit ───────────────────────────────────────────────────────────────────


def audit_write(event_id: str, kind: str, payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit (event_id, kind, payload, at) VALUES (?, ?, ?, ?)",
            (event_id, kind, json.dumps(payload, default=str), now_iso()),
        )
        conn.commit()


def reveal_create(*, order_id: str, reveal_token: str, orderer_name: str,
                  orderer_contact: str, party_date: str,
                  pickup_or_delivery: str, delivery_address: str | None,
                  guest_count: int, cake_size_kg: float,
                  decorations: str | None, notes_to_baker: str | None) -> bool:
    """Insert a new pending_reveal row. Returns False if order_id already existed."""
    ts = now_iso()
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO reveal_orders ("
                "order_id, reveal_token, orderer_name, orderer_contact, "
                "party_date, pickup_or_delivery, delivery_address, guest_count, "
                "cake_size_kg, decorations, notes_to_baker, state, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, "
                "'pending_reveal', ?, ?)",
                (order_id, reveal_token, orderer_name, orderer_contact,
                 party_date, pickup_or_delivery, delivery_address, guest_count,
                 cake_size_kg, decorations, notes_to_baker, ts, ts),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def reveal_get_by_token(reveal_token: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM reveal_orders WHERE reveal_token = ?",
            (reveal_token,),
        ).fetchone()
        return dict(row) if row else None


def reveal_get_by_order_id(order_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM reveal_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        return dict(row) if row else None


def reveal_lock_gender(reveal_token: str, gender: str,
                        knower_ip_hash: str | None) -> tuple[bool, dict | None]:
    """Write-once gender lock. Returns (newly_locked, row).

    If the row is already revealed (or further along), returns (False, row)
    without mutating — idempotent for double-submits.
    """
    if gender not in ("boy", "girl"):
        raise ValueError(f"invalid gender: {gender}")
    ts = now_iso()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM reveal_orders WHERE reveal_token = ?",
            (reveal_token,),
        ).fetchone()
        if not row:
            return False, None
        if row["state"] != "pending_reveal" or row["gender"] is not None:
            return False, dict(row)
        conn.execute(
            "UPDATE reveal_orders SET gender = ?, gender_set_at = ?, "
            "knower_ip_hash = ?, state = 'revealed', updated_at = ? "
            "WHERE reveal_token = ? AND state = 'pending_reveal' AND gender IS NULL",
            (gender, ts, knower_ip_hash, ts, reveal_token),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM reveal_orders WHERE reveal_token = ?",
            (reveal_token,),
        ).fetchone()
        return True, dict(row) if row else None


def reveal_set_state(order_id: str, state: str) -> None:
    valid = ("pending_reveal", "revealed", "baking", "ready", "delivered", "cancelled")
    if state not in valid:
        raise ValueError(f"invalid state: {state}")
    with connect() as conn:
        conn.execute(
            "UPDATE reveal_orders SET state = ?, updated_at = ? WHERE order_id = ?",
            (state, now_iso(), order_id),
        )
        conn.commit()


def audit_recent(kind: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM audit"
    params: list = []
    if kind:
        sql += " WHERE kind = ?"
        params.append(kind)
    sql += " ORDER BY at DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict] = []
    for row in rows:
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out
