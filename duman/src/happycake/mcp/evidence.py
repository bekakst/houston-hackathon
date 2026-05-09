"""Evidence MCP client — appends to the audit trail.

Every customer-facing reply ends with a one-line audit entry so the
evaluator can verify that grounded tools were called.
"""

from __future__ import annotations

import secrets

from happycake.schemas import Evidence
from happycake.storage import audit_write


def write(kind: str, payload: dict) -> str:
    event_id = secrets.token_hex(8)
    audit_write(event_id=event_id, kind=kind, payload=payload)
    return event_id


def write_evidence_chain(thread_id: str, evidence: list[Evidence]) -> str:
    return write(
        kind="reply_evidence",
        payload={
            "thread_id": thread_id,
            "evidence": [e.model_dump(mode="json") for e in evidence],
        },
    )
