"""Gateway health + tunnel registration helpers."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from happycake.agents.cli import health_check as claude_health
from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.settings import settings

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health() -> dict:
    h = hosted_mcp()
    mcp_ok = False
    mcp_tools = 0
    mcp_err: str | None = None
    if h.is_configured():
        try:
            tools = await h.list_tools()
            mcp_tools = len(tools)
            mcp_ok = mcp_tools > 0
        except MCPError as exc:
            mcp_err = str(exc)
    return {
        "ok": True,
        "env": settings.env,
        "mcp": {
            "configured": h.is_configured(),
            "ok": mcp_ok,
            "tools": mcp_tools,
            "error": mcp_err,
        },
        "claude_cli": await claude_health(),
        "telegram_bot_configured": settings.telegram_owner_bot_token.get_secret_value()
            not in ("", "missing"),
    }


@router.post("/admin/register-webhooks")
async def register_webhooks(public_base_url: str | None = None) -> dict:
    """Register the public tunnel URL with the MCP for inbound channel events."""
    base = public_base_url or settings.public_base_url
    if not base or "localhost" in base:
        raise HTTPException(
            status_code=400,
            detail=("PUBLIC_BASE_URL is missing or localhost — set it to your "
                    "ngrok / cloudflared URL before registering."),
        )
    h = hosted_mcp()
    results: dict = {}
    for tool, suffix in (
        ("whatsapp_register_webhook", "/whatsapp"),
        ("instagram_register_webhook", "/instagram"),
    ):
        try:
            r = await h.call_tool(tool, {"url": f"{base.rstrip('/')}{suffix}"})
            results[tool] = {"ok": True, "result": r}
        except MCPError as exc:
            results[tool] = {"ok": False, "error": str(exc)}
    return {"public_base_url": base, "results": results}
