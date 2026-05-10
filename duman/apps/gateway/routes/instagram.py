"""Instagram webhook receiver. Same idempotency + HMAC pattern as WhatsApp."""

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
router = APIRouter(prefix="/instagram", tags=["instagram"])


@router.get("")
async def verify(hub_mode: str = "", hub_challenge: str = "",
                 hub_verify_token: str = "") -> int | dict:
    expected = settings.instagram_verify_token.get_secret_value()
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
    secret = settings.instagram_app_secret.get_secret_value()
    if not verify_meta_signature(app_secret=secret, body=body,
                                 signature_header=x_hub_signature_256):
        raise HTTPException(status_code=403, detail="invalid signature")

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    thread_id, sender, text, received_at = _extract(payload)
    if not text:
        return {"ok": True, "skipped": "no text content"}

    external_id = derive_external_id(
        channel="instagram", sender=sender, received_at=received_at, text=text,
    )
    cached = event_get(external_id)
    if cached:
        log.info("instagram replay hit external_id=%s", external_id)
        return {"ok": True, "external_id": external_id, "replay": True,
                "cached": json.loads(cached["response"]) if cached.get("response") else None}

    event_insert(
        external_id=external_id, channel="instagram", sender=sender,
        text=text, received_at=received_at, response=None,
    )
    reply = await handle_customer_message(
        channel=Channel.instagram,
        sender=sender,
        sender_name=sender,
        text=text,
        thread_id=thread_id or f"ig_{sender}",
    )
    event_set_response(external_id, reply.model_dump_json())
    return {"ok": True, "external_id": external_id,
            "reply": reply.model_dump(mode="json")}


def _extract(payload: dict) -> tuple[str, str, str, str]:
    """Sandbox shape:
        {"threadId": "...", "from": "...", "message": "...", "ts": "..."}
    Meta IG webhook DM shape:
        {"entry":[{"messaging":[{"sender":{"id":"..."},"message":{"text":"..."},"timestamp":...}]}]}
    Meta IG webhook comments/mentions shape:
        {"entry":[{"changes":[{"field":"comments","value":{"id":"...","text":"...","from":{"id":"...","username":"..."},"media":{"id":"..."}}}]}]}
        {"entry":[{"changes":[{"field":"mentions","value":{"comment_id":"...","media_id":"..."}}]}]}
    Mentions arrive without text — Meta expects a follow-up Graph API fetch on
    comment_id/media_id. We surface them as text-less so the receiver responds
    with a clear skip rather than 500-ing or silently dropping.
    """
    if "from" in payload and ("message" in payload or "text" in payload):
        thread_id = str(payload.get("threadId") or payload.get("thread_id") or "")
        sender = str(payload.get("from", "unknown"))
        text = str(payload.get("message") or payload.get("text") or "")
        ts = str(payload.get("ts") or datetime.now(tz=timezone.utc).isoformat())
        return thread_id, sender, text, ts

    try:
        entry = payload["entry"][0]
    except (KeyError, IndexError, TypeError):
        return "", "unknown", "", datetime.now(tz=timezone.utc).isoformat()

    entry_ts = str(entry.get("time") or datetime.now(tz=timezone.utc).isoformat())

    if entry.get("messaging"):
        try:
            m = entry["messaging"][0]
            sender = str(m["sender"]["id"])
            text = (m.get("message") or {}).get("text", "")
            ts = str(m.get("timestamp") or entry_ts)
            return f"ig_{sender}", sender, text, ts
        except (KeyError, IndexError, TypeError):
            return "", "unknown", "", entry_ts

    if entry.get("changes"):
        try:
            change = entry["changes"][0]
        except (IndexError, TypeError):
            return "", "unknown", "", entry_ts
        field = str(change.get("field") or "")
        value = change.get("value") or {}
        if field == "comments":
            comment_id = str(value.get("id") or "")
            frm = value.get("from") or {}
            sender = str(frm.get("username") or frm.get("id") or "unknown")
            text = str(value.get("text") or "")
            thread_id = f"ig_c_{comment_id}" if comment_id else f"ig_c_{sender}"
            ts = str(value.get("created_time") or value.get("timestamp") or entry_ts)
            return thread_id, sender, text, ts
        if field == "mentions":
            comment_id = str(value.get("comment_id") or value.get("id") or "")
            media_id = str(value.get("media_id") or "")
            sender = str(value.get("username") or comment_id or media_id or "unknown")
            text = str(value.get("text") or "")
            thread_id = (f"ig_m_{comment_id}" if comment_id
                         else f"ig_m_{media_id}" if media_id
                         else f"ig_m_{sender}")
            return thread_id, sender, text, entry_ts

    return "", "unknown", "", entry_ts
