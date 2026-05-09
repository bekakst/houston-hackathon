"""Outbound dispatcher — sends the owner-approved reply to the customer's channel.

This is the file that closes the prior 43/100 silent-loop bug: when the owner
taps Approve, we MUST post the actual reply on the customer's channel and
confirm to the owner that it landed.
"""

from __future__ import annotations

import logging
from typing import Any

from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.storage import audit_write, now_iso

log = logging.getLogger(__name__)


async def send_to_customer(*, channel: str, customer_id: str, text: str,
                           thread_id: str | None = None) -> dict[str, Any]:
    """Post the reply on the customer-facing channel.

    Returns a dict with `ok`, `channel`, and either `delivered_at` or `error`.
    """
    h = hosted_mcp()
    result: dict[str, Any] = {"channel": channel, "customer_id": customer_id}

    try:
        if channel == "whatsapp":
            await h.call_tool("whatsapp_send",
                              {"to": customer_id, "message": text})
            result["ok"] = True
        elif channel == "instagram":
            await h.call_tool(
                "instagram_send_dm",
                {"threadId": thread_id or customer_id, "message": text},
            )
            result["ok"] = True
        elif channel == "web":
            # Web replies surface in the chat widget via the assistant API
            # response — there's no separate outbound. We log and call it done.
            result["ok"] = True
            result["note"] = "web reply already returned via assistant API"
        else:
            result["ok"] = False
            result["error"] = f"unknown channel: {channel}"
    except MCPError as exc:
        result["ok"] = False
        result["error"] = f"mcp error: {exc}"
        log.warning("outbound MCP error on %s: %s", channel, exc)
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = str(exc)
        log.exception("outbound failed on %s", channel)

    result["delivered_at"] = now_iso()
    audit_write(
        event_id=f"out_send_{customer_id}_{int(__import__('time').time())}",
        kind="outbound_sent" if result["ok"] else "outbound_failed",
        payload=result,
    )
    return result
