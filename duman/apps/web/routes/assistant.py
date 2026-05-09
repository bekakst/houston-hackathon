"""On-site assistant API. Routes through the same dispatcher as WhatsApp/IG."""

from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter

from happycake.agents.dispatcher import handle_customer_message
from happycake.schemas import Channel

router = APIRouter()


class AssistantRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    customer_id: str | None = None
    customer_name: str | None = None


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
