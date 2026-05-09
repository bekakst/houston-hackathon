"""Webhook gateway — separate FastAPI app on port 8001.

Receives inbound WhatsApp + Instagram events forwarded by the hackathon MCP,
deduplicates them via SQLite UNIQUE on events.external_id, runs them through
the agent dispatcher, and (if needed) queues an OwnerDecision for the
Telegram bot.
"""

from __future__ import annotations

from fastapi import FastAPI

from apps.gateway.routes import health, instagram, whatsapp
from happycake.storage import init_db


def create_app() -> FastAPI:
    app = FastAPI(title="HappyCake Gateway", version="0.1.0",
                  docs_url=None, redoc_url=None)
    init_db()
    app.include_router(health.router)
    app.include_router(whatsapp.router)
    app.include_router(instagram.router)
    return app


app = create_app()
