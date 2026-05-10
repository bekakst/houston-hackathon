"""Storefront pages — server-rendered, agent-readable."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from happycake.mcp import catalog
from happycake.mcp.local_data import load_policies

router = APIRouter()

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)


def _ctx(**extra) -> dict:
    return {
        "policies": load_policies(),
        "catalog": catalog.list_all(),
        **extra,
    }


def _render(request: Request, template_name: str, **extra) -> HTMLResponse:
    return templates.TemplateResponse(request, template_name, _ctx(**extra))


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return _render(request, "home.html",
                   page_title="HappyCake US — handmade cakes in Sugar Land")


@router.get("/cakes", response_class=HTMLResponse)
async def cakes(request: Request) -> HTMLResponse:
    return _render(request, "cakes_index.html",
                   page_title="Cakes — HappyCake US")


@router.get("/cakes/{slug}", response_class=HTMLResponse)
async def cake_detail(slug: str, request: Request) -> HTMLResponse:
    cake = catalog.get(slug)
    if not cake:
        raise HTTPException(status_code=404, detail=f"unknown cake: {slug}")
    return _render(request, "cake_detail.html",
                   cake=cake,
                   page_title=f'{cake.display_name()} — HappyCake US')


@router.get("/custom", response_class=HTMLResponse)
async def custom(request: Request) -> HTMLResponse:
    return _render(request, "custom.html",
                   page_title="Custom cake — HappyCake US")


@router.get("/policies/delivery", response_class=HTMLResponse)
async def policy_delivery(request: Request) -> HTMLResponse:
    return _render(request, "policy_delivery.html",
                   page_title="Delivery and pickup — HappyCake US")


@router.get("/policies/allergens", response_class=HTMLResponse)
async def policy_allergens(request: Request) -> HTMLResponse:
    return _render(request, "policy_allergens.html",
                   page_title="Allergens and dietary information — HappyCake US")


@router.get("/policies/refund", response_class=HTMLResponse)
async def policy_refund(request: Request) -> HTMLResponse:
    return _render(request, "policy_refund.html",
                   page_title="Refunds — HappyCake US")


@router.get("/faq", response_class=HTMLResponse)
async def faq(request: Request) -> HTMLResponse:
    return _render(request, "faq.html",
                   page_title="Frequently asked questions — HappyCake US")


@router.get("/contact", response_class=HTMLResponse)
async def contact(request: Request) -> HTMLResponse:
    return _render(request, "contact.html",
                   page_title="Visit us — HappyCake US")


@router.get("/order-status", response_class=HTMLResponse)
async def order_status_page(request: Request) -> HTMLResponse:
    return _render(request, "order_status.html",
                   page_title="Order status — HappyCake US")


@router.get("/agent.txt", response_class=PlainTextResponse)
async def agent_txt() -> str:
    """Hint for AI agents browsing the site."""
    return (
        "# HappyCake US — agent hint file\n"
        "User-Agent: *\n"
        "Manifest: /.well-known/agent.json\n"
        "Catalog-Endpoint: /agent/catalog.json\n"
        "Assistant-Endpoint: /assistant/message\n"
        "Suggested-Rate-Limit: 10rpm\n"
        "\n"
        "Use the manifest to discover endpoints and the cake-spec schema instead of\n"
        "scraping HTML. Server-rendered pages also expose JSON-LD on every product page.\n"
    )
