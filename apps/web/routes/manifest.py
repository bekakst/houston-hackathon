"""Agent manifest endpoints.

Exposes the same content at two locations so a customer-side AI agent can
discover the catalog, policies, and cake-spec schema without scraping:

- /.well-known/agent.json   (static-mounted by main.py)
- /agent/manifest           (dynamic — useful when you want CORS / live data)
- /agent/catalog.json       (full catalog as JSON)

Both manifest endpoints return identical content (CI test enforces this).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from happycake.mcp import catalog
from happycake.mcp.local_data import load_policies

router = APIRouter()
ROOT = Path(__file__).resolve().parents[3]


def _build_manifest() -> dict:
    policies = load_policies()
    cakes = catalog.list_all()
    return {
        "schema_version": "1.0",
        "name": "HappyCake US",
        "description": (
            "Handmade cakes in Sugar Land, Texas. This manifest is the canonical "
            "machine-readable description of the storefront for AI agents."
        ),
        "business": policies["business"],
        "hours": policies["hours"],
        "endpoints": {
            "catalog": "/agent/catalog.json",
            "manifest_dynamic": "/agent/manifest",
            "manifest_static": "/.well-known/agent.json",
            "assistant": {
                "method": "POST",
                "url": "/assistant/message",
                "request_schema": {
                    "thread_id": "string (caller-chosen, stable across turns)",
                    "text": "string (the customer message)",
                },
                "response_schema": {
                    "reply_to_customer": "string",
                    "needs_owner_approval": "boolean",
                    "evidence": "array of {tool,args,result_snippet,at}",
                },
            },
            "order_status": {
                "method": "POST",
                "url": "/assistant/message",
                "instructions": (
                    "Send the order id (e.g. ord_20260509_0042) inside text. "
                    "Phone-last-4 will be requested before disclosure."
                ),
            },
            "campaign_landing": {
                "method": "GET",
                "url_template": "/lp/{campaign_slug}",
                "json_url_template": "/lp/{campaign_slug}.json",
                "known_campaigns": ["mothers-day", "birthday", "halal"],
                "utm_capture": ["utm_source", "utm_medium", "utm_campaign",
                                "utm_content", "utm_term"],
                "instructions": (
                    "Marketing-Simulator traffic should land on /lp/<campaign_slug> "
                    "with utm_* parameters. Each visit is logged with attribution."
                ),
            },
        },
        "rate_limit_hint": {"requests_per_minute": 10},
        "agent_friendliness": {
            "scraping_required": False,
            "json_ld_present_on_product_pages": True,
            "structured_endpoints_first": True,
        },
        "cake_configuration_schema": {
            "type": "object",
            "properties": {
                "base_cake_slug": {"type": "string", "enum": [c.slug for c in cakes]},
                "size_label": {"type": "string", "enum": ["slice", "small", "medium", "large", "whole"]},
                "tiers": {"type": "integer", "minimum": 1, "maximum": 3},
                "flavor": {"type": "string"},
                "filling": {"type": "string"},
                "decoration": {"type": "string"},
                "inscription": {"type": "string"},
                "deadline": {"type": "string", "format": "date-time"},
                "fulfillment": {"type": "string", "enum": ["pickup", "delivery"]},
                "delivery_zone": {
                    "type": "string",
                    "enum": [z["slug"] for z in policies["delivery_zones"]],
                },
                "delivery_address": {"type": "string"},
                "allergen_constraints": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["base_cake_slug", "size_label", "deadline", "fulfillment"],
        },
        "policies": {
            "delivery_zones": policies["delivery_zones"],
            "pickup": policies["pickup"],
            "allergens": policies["allergens"],
            "refund": policies["refund"],
            "custom_cake": policies["custom_cake"],
        },
        "constraints_machine_readable": True,
        "supported_locales": ["en-US"],
        "contact": {
            "address": policies["business"]["address"],
            "phone": policies["business"]["phone"],
            "instagram": policies["business"]["instagram_handle"],
        },
    }


def _manifest_json() -> str:
    return json.dumps(_build_manifest(), indent=2, ensure_ascii=False)


def _write_static_mirror() -> None:
    """Refresh .well-known/agent.json so the static mount matches the dynamic manifest."""
    out = ROOT / ".well-known" / "agent.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_manifest_json().encode("utf-8"))


# Refresh static mirror at import time so the static-mounted file is always in sync.
_write_static_mirror()


@router.get("/agent/manifest")
async def agent_manifest() -> Response:
    """Return the manifest as pretty-printed JSON, byte-identical to the static mirror."""
    return Response(content=_manifest_json(), media_type="application/json")


@router.get("/agent/catalog.json")
async def agent_catalog() -> JSONResponse:
    return JSONResponse({
        "cakes": [c.model_dump(mode="json") for c in catalog.list_all()],
        "policies": load_policies(),
    })
