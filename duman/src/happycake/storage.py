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
