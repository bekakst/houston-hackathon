"""HappyCake US storefront — FastAPI + Jinja2 SSR.

Every page is server-rendered, every product page emits Bakery + Product +
Offer + FAQPage JSON-LD, and the on-site assistant is wired through the same
agent dispatcher as the WhatsApp / Instagram gateway.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from apps.web.routes import assistant, manifest, pages
from happycake.storage import init_db

ROOT = Path(__file__).resolve().parents[2]


def create_app() -> FastAPI:
    app = FastAPI(title="HappyCake US", version="0.1.0", docs_url=None, redoc_url=None)
    init_db()
    app.mount("/static", StaticFiles(directory=ROOT / "apps/web/static"), name="static")
    app.mount(
        "/.well-known",
        StaticFiles(directory=ROOT / ".well-known"),
        name="well-known",
    )
    app.include_router(pages.router)
    app.include_router(assistant.router)
    app.include_router(manifest.router)
    return app


app = create_app()
