"""On-site assistant API. Routes through the same dispatcher as WhatsApp/IG.

Two-phase contract so the conversation survives page navigation:
  POST /assistant/submit  -> { request_id }       (returns immediately)
  GET  /assistant/result/{request_id} -> { status, reply? }

The dispatcher work runs as an asyncio task on the server's event loop, so a
client navigating to another page cancels its fetch but never the work. The
next page reuses the persisted request_id to fetch the reply.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from happycake.agents.dispatcher import handle_customer_message
from happycake.schemas import Channel

router = APIRouter()


class AssistantRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    customer_id: str | None = None
    customer_name: str | None = None


# In-memory job table. Keyed by request_id. Entries auto-expire 10 min after
# completion to bound memory in long-running processes.
_JOB_TTL_S = 600
_jobs: dict[str, dict[str, Any]] = {}


def _gc_jobs() -> None:
    now = time.time()
    stale = [
        rid for rid, j in _jobs.items()
        if j.get("done_at") and now - j["done_at"] > _JOB_TTL_S
    ]
    for rid in stale:
        _jobs.pop(rid, None)


async def _run_job(request_id: str, req: AssistantRequest) -> None:
    try:
        reply = await handle_customer_message(
            channel=Channel.web,
            sender=req.customer_id or req.thread_id,
            sender_name=req.customer_name or "guest",
            text=req.text,
            thread_id=req.thread_id,
        )
        _jobs[request_id].update(
            status="done",
            reply=reply.model_dump(mode="json"),
            done_at=time.time(),
        )
    except Exception as exc:  # noqa: BLE001
        _jobs[request_id].update(
            status="error",
            error=str(exc)[:500],
            done_at=time.time(),
        )


@router.post("/assistant/submit")
async def assistant_submit(req: AssistantRequest) -> dict:
    """Kick off the dispatcher and return a request_id to poll."""
    _gc_jobs()
    request_id = secrets.token_hex(8)
    _jobs[request_id] = {
        "status": "pending",
        "thread_id": req.thread_id,
        "started_at": time.time(),
    }
    # Fire-and-forget: the task is owned by the event loop, not the request.
    asyncio.create_task(_run_job(request_id, req))
    return {"request_id": request_id, "status": "pending"}


@router.get("/assistant/result/{request_id}")
async def assistant_result(request_id: str) -> dict:
    job = _jobs.get(request_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown request_id")
    if job["status"] == "pending":
        return {"status": "pending"}
    if job["status"] == "done":
        return {"status": "done", "reply": job["reply"]}
    return {"status": "error", "error": job.get("error", "unknown error")}


# Legacy synchronous endpoint kept for the gateway / scenario tests / curl
# checks. New browser code uses /submit + /result.
@router.post("/assistant/message")
async def assistant_message(req: AssistantRequest) -> dict:
    reply = await handle_customer_message(
        channel=Channel.web,
        sender=req.customer_id or req.thread_id,
        sender_name=req.customer_name or "guest",
        text=req.text,
        thread_id=req.thread_id,
    )
    return reply.model_dump(mode="json")
