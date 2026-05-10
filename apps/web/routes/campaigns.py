"""Campaign landing pages with UTM attribution capture.

Routes:
    GET /lp/<campaign_slug>       — render the landing page, log a visit row
    GET /lp/<campaign_slug>.json  — same data as JSON for AI agents

Every visit writes an audit row keyed `campaign_visit` with the UTM
parameters and the referrer so `evaluator_get_evidence_summary` and the
internal `/replay` command can attribute Marketing-Simulator traffic.

The campaign catalog is intentionally small and brand-aware: each entry
chooses one classic and matches the brand book's voice. Marketing-Simulator
campaigns can use any campaign id — unknown slugs render a generic
landing page so a campaign created at runtime via `marketing_create_campaign`
still has somewhere to send traffic.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from happycake.mcp import catalog
from happycake.mcp.local_data import load_policies
from happycake.storage import audit_write

router = APIRouter()

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)


CAMPAIGNS: dict[str, dict] = {
    "mothers-day": {
        "eyebrow": "Mother's Day",
        "headline": "The cake that tastes like the one she made.",
        "lead": "Pick up a classic the morning of, or have it delivered in Sugar Land, Missouri City, Stafford, or Bellaire.",
        "body_title": "Order by Saturday",
        "body": "Cake \"Honey\" and cake \"Milk Maiden\" are bakeable on 4 hours notice. Cake \"Tiramisu\" needs 24. We'll text when it's ready.",
        "cta_label": "Choose a cake",
        "cta_href": "/cakes",
        "aside_title": "Pickup or delivery",
        "aside_body": "350 Promenade Way, Suite 500.<br>Same-day pickup before 4 PM.<br>Local delivery from $6.",
        "featured_slugs": ["honey", "milk-maiden", "tiramisu"],
        "featured_title": "Three she'll recognise",
    },
    "birthday": {
        "eyebrow": "Birthdays",
        "headline": "A cake for the table, baked here in Sugar Land.",
        "lead": "Tell us the day, the size, and the name to write on it. We'll do the rest.",
        "body_title": "Plan the day",
        "body": "For ten guests we recommend a 1.2 kg whole cake. For larger groups, a custom cake takes 48 hours.",
        "cta_label": "Start a custom cake",
        "cta_href": "/custom",
        "aside_title": "Halal-friendly classics",
        "aside_body": "All our classics are halal-friendly. We list every allergen on every cake page.",
        "featured_slugs": ["honey", "napoleon", "milk-maiden", "pistachio-roll"],
        "featured_title": "Crowd-pleasers",
    },
    "halal": {
        "eyebrow": "Halal-friendly",
        "headline": "Real cakes, made by hand. Halal-friendly classics, every day.",
        "lead": "Eight cakes on the counter, all halal-friendly, with allergens listed on every page.",
        "body_title": "What's on the counter today",
        "body": "Cake \"Honey\", cake \"Milk Maiden\", cake \"Pistachio Roll\". For larger orders, message us a day ahead.",
        "cta_label": "See all cakes",
        "cta_href": "/cakes",
        "aside_title": "Visit",
        "aside_body": "350 Promenade Way, Sugar Land.<br>Tue–Sat 11–7, Sun 12–6.",
        "featured_slugs": ["honey", "milk-maiden", "pistachio-roll", "tiramisu"],
        "featured_title": "Today's bake",
    },
}


_GENERIC: dict = {
    "eyebrow": "HappyCake · Sugar Land",
    "headline": "Real cakes, made by hand.",
    "lead": "Same recipes as the day we opened. Pickup or local delivery.",
    "body_title": "On the counter today",
    "body": "Cake \"Honey\", cake \"Napoleon\", cake \"Milk Maiden\". Slice or whole. 4 hours notice for the daily classics, 24 for the rest.",
    "cta_label": "See the cakes",
    "cta_href": "/cakes",
    "aside_title": "Pickup or delivery",
    "aside_body": "350 Promenade Way, Suite 500.<br>Tue–Sat 11–7, Sun 12–6.",
    "featured_slugs": ["honey", "napoleon", "milk-maiden", "pistachio-roll"],
    "featured_title": "Four to start with",
}


_UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")


def _attribution(request: Request, campaign_slug: str) -> dict:
    qp = request.query_params
    return {
        "campaign_slug": campaign_slug,
        "utm_source": qp.get("utm_source"),
        "utm_medium": qp.get("utm_medium"),
        "utm_campaign": qp.get("utm_campaign") or campaign_slug,
        "utm_content": qp.get("utm_content"),
        "utm_term": qp.get("utm_term"),
        "referer": request.headers.get("referer"),
        "user_agent": (request.headers.get("user-agent") or "")[:160],
    }


def _tracking_qs(attribution: dict) -> str:
    """Build a ?utm_… query string to forward attribution from the LP into /cakes."""
    pairs = [(k, attribution.get(k)) for k in _UTM_KEYS]
    parts = [f"{k}={v}" for k, v in pairs if v]
    return ("?" + "&".join(parts)) if parts else ""


def _campaign(slug: str) -> dict:
    return {**_GENERIC, **CAMPAIGNS.get(slug, {})}


def _featured(slug: str):
    cfg = _campaign(slug)
    out = []
    for s in cfg.get("featured_slugs", []):
        cake = catalog.get(s)
        if cake:
            out.append(cake)
    return out


def _record_visit(slug: str, attribution: dict) -> None:
    audit_write(
        event_id=f"camp_{secrets.token_hex(6)}",
        kind="campaign_visit",
        payload={"campaign_slug": slug, **attribution},
    )


@router.get("/lp/{campaign_slug}", response_class=HTMLResponse)
async def landing_page(campaign_slug: str, request: Request) -> HTMLResponse:
    attribution = _attribution(request, campaign_slug)
    _record_visit(campaign_slug, attribution)
    ctx = {
        "policies": load_policies(),
        "catalog": catalog.list_all(),
        "campaign": _campaign(campaign_slug),
        "campaign_slug": campaign_slug,
        "featured": _featured(campaign_slug),
        "attribution": attribution,
        "tracking_qs": _tracking_qs(attribution),
        "page_title": f"{_campaign(campaign_slug)['headline']} — HappyCake US",
    }
    return templates.TemplateResponse(request, "landing.html", ctx)


@router.get("/lp/{campaign_slug}.json")
async def landing_json(campaign_slug: str, request: Request) -> JSONResponse:
    attribution = _attribution(request, campaign_slug)
    _record_visit(campaign_slug, attribution)
    cfg = _campaign(campaign_slug)
    return JSONResponse({
        "campaign_slug": campaign_slug,
        "campaign": cfg,
        "featured": [c.model_dump(mode="json") for c in _featured(campaign_slug)],
        "attribution": attribution,
        "endpoints": {"assistant": "/assistant/message", "manifest": "/.well-known/agent.json"},
    })
