"""Hosted MCP client.

The hackathon sandbox exposes one endpoint:
    https://www.steppebusinessclub.com/api/mcp
authenticated with a per-team bearer token. We use httpx.AsyncClient + JSON-RPC
over HTTP for tools/list and tools/call (the MCP protocol's HTTP transport).

When MCP_TEAM_TOKEN is unset or "missing", every call short-circuits and the
local YAML-backed clients are used instead, so a fresh clone runs without the
token.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from happycake.settings import settings

log = logging.getLogger(__name__)

_MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    """Raised when the hosted MCP returns an error or the call times out."""


class HostedMCP:
    """Thin async client over the Steppe Business Club hosted MCP."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._tools_cache: list[dict] | None = None
        self._next_id = 0

    def is_configured(self) -> bool:
        token = settings.mcp_team_token.get_secret_value()
        return bool(token) and token != "missing"

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    token = settings.mcp_team_token.get_secret_value()
                    self._client = httpx.AsyncClient(
                        headers={
                            # The Steppe Business Club hackathon MCP uses an
                            # opaque X-Team-Token header, not Authorization: Bearer.
                            # Per GET / response: "auth": "X-Team-Token header (opaque)".
                            "X-Team-Token": token,
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        timeout=httpx.Timeout(20.0, connect=5.0),
                        follow_redirects=True,
                    )
        return self._client

    def _next_request_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _rpc(self, method: str, params: dict | None = None) -> Any:
        if not self.is_configured():
            raise MCPError("MCP_TEAM_TOKEN is not set")
        client = await self._ensure_client()
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": method,
            "params": params or {},
        }
        try:
            resp = await client.post(settings.mcp_base_url, json=payload)
        except httpx.HTTPError as exc:
            raise MCPError(f"transport error: {exc}") from exc
        if resp.status_code >= 400:
            raise MCPError(f"http {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if "error" in body:
            raise MCPError(f"rpc error: {body['error']}")
        return body.get("result")

    async def list_tools(self) -> list[dict]:
        if self._tools_cache is not None:
            return self._tools_cache
        try:
            result = await self._rpc("tools/list")
            self._tools_cache = result.get("tools", []) if isinstance(result, dict) else []
        except MCPError as exc:
            log.warning("MCP tools/list failed: %s", exc)
            self._tools_cache = []
        return self._tools_cache

    async def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict | list:
        """Call a tool and unwrap the MCP `content[].text` envelope.

        The hackathon MCP server returns:
            {"content": [{"type": "text", "text": "<json string>"}]}
        We parse the inner JSON and return it directly. Multiple text parts are
        concatenated and re-parsed; non-text parts are returned as-is.
        """
        import json as _json
        result = await self._rpc(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )
        if not isinstance(result, dict):
            raise MCPError(f"unexpected tools/call result shape: {type(result).__name__}")

        content = result.get("content")
        if not isinstance(content, list):
            return result

        text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        if text_parts:
            joined = "".join(text_parts)
            try:
                return _json.loads(joined)
            except _json.JSONDecodeError:
                return {"text": joined}
        return result

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_singleton: HostedMCP | None = None


def hosted_mcp() -> HostedMCP:
    global _singleton
    if _singleton is None:
        _singleton = HostedMCP()
    return _singleton
