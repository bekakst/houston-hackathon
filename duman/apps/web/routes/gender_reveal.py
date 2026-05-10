"""Blind gender-reveal cake order — orderer never sees the gender, the
"knower" submits it through a one-time reveal link, and the kitchen bakes
the surprise.

The gender is write-once and never reaches an orderer-facing template
context (defense-in-depth via RevealOrdererView, which has no gender field).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from happycake.mcp import catalog
from happycake.mcp.local_data import load_policies
from happycake.schemas import (
    RevealKnowerView,
    RevealOrderCreate,
    RevealOrdererView,
    RevealState,
)
from happycake.settings import settings
from happycake.storage import (
    audit_write,
    decision_insert,
    now_iso,
    reveal_create,
    reveal_get_by_order_id,
    reveal_get_by_token,
    reveal_lock_gender,
)

log = logging.getLogger(__name__)

router = APIRouter()

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)


def _ctx(**extra) -> dict:
    return {
        "policies": load_policies(),
        "catalog": catalog.list_all(),
        **extra,
    }


def _new_order_id() -> str:
    today = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    return f"HC-RV-{today}-{secrets.token_hex(2).upper()}"


def _new_reveal_token() -> str:
    return secrets.token_urlsafe(18)


def _new_decision_id() -> str:
    return secrets.token_hex(6)


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    salt = settings.reveal_token_salt.get_secret_value().encode("utf-8")
    return hashlib.sha256(salt + ip.encode("utf-8")).hexdigest()


def _redacted_token(token: str) -> str:
    return f"{token[:6]}…" if token else "?"


def _size_kg_for_guests(guest_count: int) -> float:
    """Map guest count to standard cake size, mirroring the brandbook bands.

    - up to 8 guests -> 1.0 kg (small)
    - 9 to 15 -> 1.5 kg (medium)
    - 16 and up -> 2.5 kg (large)
    """
    if guest_count <= 8:
        return 1.0
    if guest_count <= 15:
        return 1.5
    return 2.5


_STATE_LABELS: dict[str, str] = {
    "pending_reveal": "We're waiting for the secret. Once your trusted person opens the link, we'll start baking.",
    "revealed":       "The secret is safe with us. The kitchen is preparing your cake.",
    "baking":         "In the oven. Your cake is on the way.",
    "ready":          "Ready for pickup. See you soon.",
    "delivered":      "Delivered. Enjoy the surprise.",
    "cancelled":      "Order cancelled. Send a message on WhatsApp if this is unexpected.",
}


def _knower_has_responded(state: str) -> bool:
    return state in ("revealed", "baking", "ready", "delivered")


def _orderer_view(row: dict, *, reveal_url: str | None = None) -> RevealOrdererView:
    """Build the orderer-facing view. The `gender` column is never read."""
    return RevealOrdererView(
        order_id=row["order_id"],
        orderer_name=row["orderer_name"],
        party_date=row["party_date"],
        pickup_or_delivery=row["pickup_or_delivery"],
        delivery_address=row.get("delivery_address"),
        guest_count=row["guest_count"],
        cake_size_kg=row["cake_size_kg"],
        decorations=row.get("decorations"),
        notes_to_baker=row.get("notes_to_baker"),
        state=RevealState(row["state"]),
        state_label=_STATE_LABELS.get(row["state"], row["state"]),
        reveal_url=reveal_url,
        knower_has_responded=_knower_has_responded(row["state"]),
    )


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("/order/gender-reveal", response_class=HTMLResponse)
async def gender_reveal_landing(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "gender_reveal_landing.html",
        _ctx(page_title='Gender-reveal cake — HappyCake US'),
    )


@router.post("/order/gender-reveal")
async def gender_reveal_submit(
    request: Request,
    orderer_name: str = Form(...),
    orderer_contact: str = Form(...),
    party_date: str = Form(...),
    pickup_or_delivery: str = Form(...),
    delivery_address: str | None = Form(default=None),
    guest_count: int = Form(...),
    decorations: str | None = Form(default=None),
    notes_to_baker: str | None = Form(default=None),
):
    try:
        payload = RevealOrderCreate(
            orderer_name=orderer_name.strip(),
            orderer_contact=orderer_contact.strip(),
            party_date=party_date.strip(),
            pickup_or_delivery=pickup_or_delivery.strip(),
            delivery_address=(delivery_address or "").strip() or None,
            guest_count=guest_count,
            decorations=(decorations or "").strip() or None,
            notes_to_baker=(notes_to_baker or "").strip() or None,
        )
    except ValidationError as exc:
        log.info("gender_reveal form validation failed: %s", exc)
        return templates.TemplateResponse(
            request, "gender_reveal_landing.html",
            _ctx(page_title='Gender-reveal cake — HappyCake US',
                 form_errors=exc.errors(),
                 form_values={"orderer_name": orderer_name,
                              "orderer_contact": orderer_contact,
                              "party_date": party_date,
                              "pickup_or_delivery": pickup_or_delivery,
                              "delivery_address": delivery_address,
                              "guest_count": guest_count,
                              "decorations": decorations,
                              "notes_to_baker": notes_to_baker}),
            status_code=400,
        )

    if payload.pickup_or_delivery == "delivery" and not payload.delivery_address:
        return templates.TemplateResponse(
            request, "gender_reveal_landing.html",
            _ctx(page_title='Gender-reveal cake — HappyCake US',
                 form_errors=[{"loc": ("delivery_address",),
                               "msg": "Delivery address is required for delivery."}],
                 form_values=payload.model_dump()),
            status_code=400,
        )

    order_id = _new_order_id()
    reveal_token = _new_reveal_token()
    cake_size_kg = _size_kg_for_guests(payload.guest_count)

    inserted = reveal_create(
        order_id=order_id,
        reveal_token=reveal_token,
        orderer_name=payload.orderer_name,
        orderer_contact=payload.orderer_contact,
        party_date=payload.party_date,
        pickup_or_delivery=payload.pickup_or_delivery,
        delivery_address=payload.delivery_address,
        guest_count=payload.guest_count,
        cake_size_kg=cake_size_kg,
        decorations=payload.decorations,
        notes_to_baker=payload.notes_to_baker,
    )
    if not inserted:
        log.warning("reveal_create collision for order_id %s — retrying", order_id)
        order_id = _new_order_id()
        inserted = reveal_create(
            order_id=order_id, reveal_token=reveal_token,
            orderer_name=payload.orderer_name,
            orderer_contact=payload.orderer_contact,
            party_date=payload.party_date,
            pickup_or_delivery=payload.pickup_or_delivery,
            delivery_address=payload.delivery_address,
            guest_count=payload.guest_count,
            cake_size_kg=cake_size_kg,
            decorations=payload.decorations,
            notes_to_baker=payload.notes_to_baker,
        )
        if not inserted:
            raise HTTPException(status_code=500, detail="could not allocate order id")

    audit_write(
        event_id=f"rv_create_{order_id}",
        kind="gender_reveal_created",
        payload={
            "order_id": order_id,
            "token_prefix": _redacted_token(reveal_token),
            "party_date": payload.party_date,
            "guest_count": payload.guest_count,
        },
    )
    return RedirectResponse(url=f"/order/{order_id}/sent-to-knower",
                            status_code=303)


@router.get("/order/{order_id}/sent-to-knower", response_class=HTMLResponse)
async def gender_reveal_sent_to_knower(order_id: str, request: Request) -> HTMLResponse:
    row = reveal_get_by_order_id(order_id)
    if not row:
        raise HTTPException(status_code=404, detail="order not found")
    base = str(request.base_url).rstrip("/")
    reveal_url = f"{base}/reveal/{row['reveal_token']}"
    view = _orderer_view(row, reveal_url=reveal_url)
    # The view is a Pydantic model whose serializer has no `gender` field —
    # passing model_dump() guarantees the template never receives it.
    return templates.TemplateResponse(
        request, "gender_reveal_sent_to_knower.html",
        _ctx(page_title='Your reveal cake — HappyCake US',
             reveal=view.model_dump(),
             share_text=(
                 "Hi — we're doing a gender-reveal cake. Please open this link "
                 "and tell HappyCake whether it's a boy or a girl. We won't "
                 "see your answer."
             )),
    )


@router.get("/order/{order_id}/status", response_class=HTMLResponse)
async def gender_reveal_status(order_id: str, request: Request) -> HTMLResponse:
    row = reveal_get_by_order_id(order_id)
    if not row:
        raise HTTPException(status_code=404, detail="order not found")
    view = _orderer_view(row)
    return templates.TemplateResponse(
        request, "gender_reveal_status.html",
        _ctx(page_title='Order status — HappyCake US',
             reveal=view.model_dump()),
    )


@router.get("/reveal/{token}", response_class=HTMLResponse)
async def reveal_pick_page(token: str, request: Request) -> HTMLResponse:
    row = reveal_get_by_token(token)
    if not row:
        raise HTTPException(status_code=404, detail="reveal link not found")
    first_name = (row["orderer_name"] or "your friend").split()[0] or "your friend"
    view = RevealKnowerView(
        order_id=row["order_id"],
        orderer_first_name=first_name,
        party_date=row["party_date"],
        cake_size_kg=row["cake_size_kg"],
        already_locked=row["state"] != "pending_reveal",
    )
    return templates.TemplateResponse(
        request, "gender_reveal_knower.html",
        _ctx(page_title='Tell HappyCake the secret',
             knower=view.model_dump(),
             reveal_token=token),
    )


@router.post("/reveal/{token}")
async def reveal_submit(token: str, request: Request,
                        gender: str = Form(...)) -> HTMLResponse:
    g = (gender or "").strip().lower()
    if g not in ("boy", "girl"):
        raise HTTPException(status_code=400, detail="gender must be 'boy' or 'girl'")

    row = reveal_get_by_token(token)
    if not row:
        raise HTTPException(status_code=404, detail="reveal link not found")

    client_ip = request.client.host if request.client else None
    ip_hash = _hash_ip(client_ip)
    newly_locked, row_after = reveal_lock_gender(token, g, ip_hash)

    first_name = (row["orderer_name"] or "your friend").split()[0] or "your friend"
    if newly_locked and row_after:
        audit_write(
            event_id=f"rv_lock_{row_after['order_id']}",
            kind="gender_reveal_locked",
            payload={
                "order_id": row_after["order_id"],
                "token_prefix": _redacted_token(token),
                # gender intentionally NOT in audit summary — present in the
                # owner's decision payload only.
            },
        )
        # Queue an OwnerDecision so the owner gets the inline-keyboard card.
        decision_id = _new_decision_id()
        summary = (
            f"🔒 Gender-reveal locked — {row_after['order_id']}\n"
            f"Orderer: {row_after['orderer_name']}\n"
            f"Contact: {row_after['orderer_contact']}\n"
            f"Party: {row_after['party_date']} · "
            f"{row_after['pickup_or_delivery']} · "
            f"{row_after['guest_count']} guests · "
            f"{row_after['cake_size_kg']} kg\n"
            f"\n"
            f"Interior: {'PINK (girl)' if g == 'girl' else 'BLUE (boy)'}"
        )
        if row_after.get("decorations"):
            summary += f"\nDecorations: {row_after['decorations']}"
        if row_after.get("notes_to_baker"):
            summary += f"\nNotes: {row_after['notes_to_baker']}"
        if row_after["pickup_or_delivery"] == "delivery" and row_after.get("delivery_address"):
            summary += f"\nAddress: {row_after['delivery_address']}"
        draft_reply = (
            f"Your reveal cake is locked in. Pickup {row_after['party_date']}. "
            "We can't wait. "
            "Order on the site at happycake.us or send a message on WhatsApp."
        )
        decision_payload = {
            "decision_id": decision_id,
            "kind": "gender_reveal",
            "channel": "web",
            "customer_id": row_after["orderer_contact"],
            "customer_name": row_after["orderer_name"],
            "thread_id": f"reveal_{row_after['order_id']}",
            "draft_reply": draft_reply,
            "summary": summary,
            "intent": "gender_reveal",
            "suggested_action": "kitchen_bake_with_interior",
            "reveal_order_id": row_after["order_id"],
            "reveal_gender": g,
            "reveal_party_date": row_after["party_date"],
            "reveal_cake_size_kg": row_after["cake_size_kg"],
            "reveal_decorations": row_after.get("decorations"),
            "reveal_notes_to_baker": row_after.get("notes_to_baker"),
            "reveal_pickup_or_delivery": row_after["pickup_or_delivery"],
            "reveal_delivery_address": row_after.get("delivery_address"),
            "created_at": now_iso(),
        }
        decision_insert(decision_id, "gender_reveal", "web",
                        row_after["orderer_contact"], decision_payload)
        log.info("gender_reveal locked order=%s token=%s",
                 row_after["order_id"], _redacted_token(token))

    return templates.TemplateResponse(
        request, "gender_reveal_knower_done.html",
        _ctx(page_title='Thank you — HappyCake US',
             orderer_first_name=first_name,
             party_date=row["party_date"]),
    )
