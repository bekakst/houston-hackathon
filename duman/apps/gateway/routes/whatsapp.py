"""WhatsApp webhook receiver.

The Steppe Business Club hosted MCP forwards inbound WhatsApp events to a URL
we register via `whatsapp_register_webhook(url)`. This route receives them,
deduplicates by sha256 external_id, and dispatches through the agent stack.

Verification (GET) and POST envelope match the Meta WhatsApp Cloud API shape
so the same code works against either source.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from apps.gateway.security import derive_external_id, verify_meta_signature
from happycake.agents.dispatcher import handle_customer_message
from happycake.schemas import Channel
from happycake.settings import settings
from happycake.storage import event_get, event_insert, event_set_response

log = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("")
async def verify(hub_mode: str = "", hub_challenge: str = "",
                 hub_verify_token: str = "") -> int | dict:
    """Meta-style verification handshake."""
    expected = settings.whatsapp_verify_token.get_secret_value()
    if hub_mode == "subscribe" and hub_verify_token == expected:
        try:
            return int(hub_challenge)
        except ValueError:
            return {"hub.challenge": hub_challenge}
    raise HTTPException(status_code=403, detail="verify token mismatch")


@router.post("")
async def receive(req: Request,
                  x_hub_signature_256: str | None = Header(default=None)) -> dict:
    body = await req.body()
    secret = settings.whatsapp_app_secret.get_secret_value()
    if not verify_meta_signature(app_secret=secret, body=body,
                                 signature_header=x_hub_signature_256):
        raise HTTPException(status_code=403, detail="invalid signature")

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    sender, text, received_at = _extract(payload)
    if not text:
        return {"ok": True, "skipped": "no text content"}

    external_id = derive_external_id(
        channel="whatsapp", sender=sender, received_at=received_at, text=text,
    )
    cached = event_get(external_id)
    if cached:
        log.info("whatsapp replay hit external_id=%s", external_id)
        return {"ok": True, "external_id": external_id, "replay": True,
                "cached": json.loads(cached["response"]) if cached.get("response") else None}

    event_insert(
        external_id=external_id, channel="whatsapp", sender=sender, text=text,
        received_at=received_at, response=None,
    )

    reply = await handle_customer_message(
        channel=Channel.whatsapp,
        sender=sender,
        sender_name=sender,
        text=text,
        thread_id=f"wa_{sender}",
    )
    event_set_response(external_id, reply.model_dump_json())
    return {"ok": True, "external_id": external_id,
            "reply": reply.model_dump(mode="json")}


def _extract(payload: dict) -> tuple[str, str, str]:
    """Pull sender, text, ts from either the Meta WA Cloud or the SBC sandbox shape.

    Meta:
        {"entry":[{"changes":[{"value":{"messages":[{"from":"...","text":{"body":"..."},"timestamp":"..."}]}}]}]}
    Sandbox (whatsapp_inject_inbound shape):
        {"from":"...", "message":"...", "ts":"..."}
    """
    if "from" in payload and ("message" in payload or "text" in payload):
        sender = str(payload.get("from", "unknown"))
        text = str(payload.get("message") or payload.get("text") or "")
        ts = str(payload.get("ts") or datetime.now(tz=timezone.utc).isoformat())
        return sender, text, ts

    try:
        msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        sender = str(msg.get("from", "unknown"))
        text = (msg.get("text") or {}).get("body", "")
        ts = str(msg.get("timestamp")
                 or datetime.now(tz=timezone.utc).isoformat())
        return sender, text, ts
    except (KeyError, IndexError, TypeError):
        return "unknown", "", datetime.now(tz=timezone.utc).isoformat()
