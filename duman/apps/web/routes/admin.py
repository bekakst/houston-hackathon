"""Admin dashboard for HappyCake — read-only, evidence-first.

Maps the 6-step customer-to-pickup lifecycle onto pages that pull from the
already-existing storage tables and MCP read tools. No new state is created
here; everything renders the truth that the dispatcher + owner-bot wrote.

Pages:
  /admin/                 Overview + lifecycle pipeline + KPIs
  /admin/orders           All orders by status (pending/approved/in_kitchen/ready/completed)
  /admin/orders/{id}      Per-order replay (audit trail + decision payload)
  /admin/decisions        Pending owner decisions queue (mirrors Telegram bot)
  /admin/kitchen          Kitchen tickets + production summary
  /admin/inventory        Per-cake availability today + 7-day view
  /admin/sales            Revenue, channels, margins (from MCP)
  /admin/marketing        $500 plan + campaign metrics
  /admin/audit            Append-only event log, filterable
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from happycake.mcp import catalog as catalog_mcp
from happycake.mcp import inventory as inventory_mcp
from happycake.mcp import marketing as marketing_mcp
from happycake.mcp import orders as orders_mcp
from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.mcp.local_data import load_policies
from happycake.storage import (
    audit_recent,
    connect,
    decision_get,
    decision_list_pending,
)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = Jinja2Templates(directory=str(ROOT / "apps/web/templates"))

router = APIRouter(prefix="/admin")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _all_decisions(limit: int = 200) -> list[dict]:
    """Read all decisions (any status) ordered newest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        d = dict(row)
        try:
            d["payload"] = json.loads(d["payload"])
        except (TypeError, json.JSONDecodeError):
            d["payload"] = {}
        out.append(d)
    return out


def _orders_table_rows(limit: int = 200) -> list[dict]:
    """Read the local `orders` table directly — created lazily by orders.draft.

    Returns [] if the table doesn't exist yet. Each row is a dict with the
    parsed payload, the status, and the order_id.
    """
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT order_id, payload, status, created_at "
                "FROM orders ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict] = []
    for row in rows:
        d = dict(row)
        try:
            d["payload"] = json.loads(d["payload"])
        except (TypeError, json.JSONDecodeError):
            d["payload"] = {}
        out.append(d)
    return out


def _audit_for_thread(thread_id: str, limit: int = 200) -> list[dict]:
    return [
        e for e in audit_recent(limit=limit)
        if (e.get("payload") or {}).get("thread_id") == thread_id
    ]


def _audit_for_decision(decision_id: str, limit: int = 200) -> list[dict]:
    out = []
    for e in audit_recent(limit=limit):
        p = e.get("payload") or {}
        if p.get("decision_id") == decision_id:
            out.append(e)
    return out


def _kpis() -> dict[str, Any]:
    decs = _all_decisions(limit=500)
    by_status: Counter[str] = Counter(d["status"] for d in decs)
    by_kind_pending: Counter[str] = Counter(
        d["kind"] for d in decs if d["status"] == "pending"
    )
    audits = audit_recent(limit=500)
    today_iso = date.today().isoformat()
    today_inbound = sum(
        1 for e in audits
        if e["kind"] == "message_inbound" and e["at"][:10] == today_iso
    )
    today_outbound = sum(
        1 for e in audits
        if e["kind"] == "message_outbound" and e["at"][:10] == today_iso
    )
    today_approved = sum(
        1 for e in audits
        if e["kind"] == "decision_approved" and e["at"][:10] == today_iso
    )
    pos_orders = sum(1 for e in audits if e["kind"] == "pos_order_created")
    kitchen_tickets = sum(1 for e in audits if e["kind"] == "kitchen_ticket_created")
    return {
        "decisions_total": len(decs),
        "decisions_by_status": dict(by_status),
        "pending_by_kind": dict(by_kind_pending),
        "today_inbound": today_inbound,
        "today_outbound": today_outbound,
        "today_approved": today_approved,
        "pos_orders_total": pos_orders,
        "kitchen_tickets_total": kitchen_tickets,
    }


async def _kitchen_summary_safe() -> dict | None:
    h = hosted_mcp()
    if not h.is_configured():
        return None
    try:
        return await h.call_tool("kitchen_get_production_summary") or {}
    except MCPError as exc:
        log.info("kitchen_get_production_summary unavailable: %s", exc)
        return None


async def _kitchen_tickets_safe() -> list[dict]:
    h = hosted_mcp()
    if not h.is_configured():
        return []
    try:
        result = await h.call_tool("kitchen_list_tickets")
    except MCPError as exc:
        log.info("kitchen_list_tickets unavailable: %s", exc)
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("tickets") or []
    return []


async def _square_recent_orders_safe(limit: int = 25) -> list[dict]:
    h = hosted_mcp()
    if not h.is_configured():
        return []
    try:
        result = await h.call_tool("square_recent_orders", {"limit": limit})
    except MCPError as exc:
        log.info("square_recent_orders unavailable: %s", exc)
        return []
    if isinstance(result, dict):
        return result.get("orders") or result.get("items") or []
    return result or []


def _recent_audit_safe(kind: str, limit: int = 20) -> list[dict]:
    """Read recent audit events of one kind. Returns [] on any failure."""
    try:
        return audit_recent(kind=kind, limit=limit)
    except (sqlite3.DatabaseError, KeyError, ValueError) as exc:
        log.info("audit_recent(%s) unavailable: %s", kind, exc)
        return []


def _pending_marketing_drafts_safe() -> list[dict]:
    """Drafts from /plan_marketing waiting for owner Approve in Telegram."""
    try:
        return decision_list_pending(kind="marketing", limit=10)
    except (sqlite3.DatabaseError, KeyError, ValueError) as exc:
        log.info("decision_list_pending(marketing) unavailable: %s", exc)
        return []


async def _marketing_sales_history_safe() -> list[dict]:
    h = hosted_mcp()
    if not h.is_configured():
        return []
    try:
        result = await h.call_tool("marketing_get_sales_history")
    except MCPError as exc:
        log.info("marketing_get_sales_history unavailable: %s", exc)
        return []
    if isinstance(result, dict):
        return result.get("history") or result.get("items") or []
    return result or []


async def _marketing_margin_by_product_safe() -> list[dict]:
    h = hosted_mcp()
    if not h.is_configured():
        return []
    try:
        result = await h.call_tool("marketing_get_margin_by_product")
    except MCPError as exc:
        log.info("marketing_get_margin_by_product unavailable: %s", exc)
        return []
    if isinstance(result, dict):
        return result.get("items") or result.get("products") or []
    return result or []


def _ctx(request: Request, **extra) -> dict:
    """Build the Jinja context. `request` is taken as a positional arg for
    callsite clarity but is NOT injected here — the new Starlette/FastAPI
    signature (`TemplateResponse(request, name, context)`) handles it for us.
    """
    base = {"policies": load_policies(), "page_title": None}
    base.update(extra)
    return base


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def admin_home(request: Request) -> HTMLResponse:
    kpis = _kpis()
    audits = audit_recent(limit=20)
    pending = decision_list_pending(limit=10)

    # Lifecycle counts: each step of the 6-step flow
    decs = _all_decisions(limit=500)
    audit_kinds: Counter[str] = Counter(e["kind"] for e in audit_recent(limit=1000))
    lifecycle = [
        {
            "step": 1,
            "title": "Customer message",
            "icon": "💬",
            "count": audit_kinds.get("message_inbound", 0),
            "detail": "Inbound on web / WhatsApp / Instagram",
        },
        {
            "step": 2,
            "title": "Owner approval queued",
            "icon": "🔔",
            "count": sum(1 for d in decs if d["status"] in ("pending", "approved", "rejected")),
            "detail": f"{kpis['pending_by_kind']} pending",
        },
        {
            "step": 3,
            "title": "POS + kitchen ticket",
            "icon": "🍰",
            "count": kpis["pos_orders_total"],
            "detail": f"{kpis['kitchen_tickets_total']} kitchen tickets created",
        },
        {
            "step": 4,
            "title": "Inventory check",
            "icon": "📦",
            "count": audit_kinds.get("inventory_checked", 0),
            "detail": "Daily caps from kitchen calendar",
        },
        {
            "step": 5,
            "title": "Ready for pickup",
            "icon": "✅",
            "count": audit_kinds.get("kitchen_ticket_ready", 0),
            "detail": "Owner taps mark-ready in Telegram",
        },
        {
            "step": 6,
            "title": "Customer notified / completed",
            "icon": "🚀",
            "count": audit_kinds.get("decision_approved", 0),
            "detail": "Reply sent on the original channel",
        },
    ]

    return TEMPLATES.TemplateResponse(
        request,
        "admin/home.html",
        _ctx(
            request,
            kpis=kpis,
            audits=audits,
            pending=pending,
            lifecycle=lifecycle,
            page_title="Admin — overview",
        ),
    )


@router.get("/orders", response_class=HTMLResponse)
async def admin_orders(request: Request) -> HTMLResponse:
    decisions = _all_decisions(limit=300)
    audits = audit_recent(limit=1000)

    # Build a per-decision lifecycle summary by stitching audits onto decisions.
    decision_audits: dict[str, list[dict]] = defaultdict(list)
    for e in audits:
        did = (e.get("payload") or {}).get("decision_id")
        if did:
            decision_audits[did].append(e)

    rows: list[dict] = []
    for d in decisions:
        payload = d.get("payload", {})
        did = d["decision_id"]
        d_audits = decision_audits.get(did, [])
        kinds = {e["kind"] for e in d_audits}
        # Map decision row + audits → lifecycle bucket
        if d["status"] == "rejected":
            bucket = "rejected"
        elif "kitchen_ticket_ready" in kinds:
            bucket = "ready"
        elif "kitchen_ticket_created" in kinds or "kitchen_ticket_accepted" in kinds:
            bucket = "in_kitchen"
        elif "pos_order_created" in kinds or d["status"] == "approved":
            bucket = "approved"
        elif d["status"] == "pending":
            bucket = "pending_owner"
        else:
            bucket = d["status"]
        rows.append({
            "decision": d,
            "payload": payload,
            "bucket": bucket,
            "audits": d_audits,
            "draft_reply_excerpt": (payload.get("draft_reply") or "")[:140],
        })

    bucket_order = ["pending_owner", "approved", "in_kitchen", "ready", "rejected"]
    by_bucket: dict[str, list[dict]] = {b: [] for b in bucket_order}
    for r in rows:
        by_bucket.setdefault(r["bucket"], []).append(r)

    square_orders = await _square_recent_orders_safe()

    return TEMPLATES.TemplateResponse(
        request,
        "admin/orders.html",
        _ctx(
            request,
            rows=rows,
            by_bucket=by_bucket,
            bucket_order=bucket_order,
            square_orders=square_orders,
            page_title="Admin — orders",
        ),
    )


@router.get("/orders/{decision_id}", response_class=HTMLResponse)
async def admin_order_detail(request: Request, decision_id: str) -> HTMLResponse:
    decision = decision_get(decision_id)
    if not decision:
        return HTMLResponse(
            "<h1>Decision not found</h1>"
            "<p><a href=\"/admin/orders\">← back to orders</a></p>",
            status_code=404,
        )
    decision_audits = _audit_for_decision(decision_id, limit=500)
    thread_id = (decision.get("payload") or {}).get("thread_id")
    thread_audits = _audit_for_thread(thread_id, limit=500) if thread_id else []
    # Merge + dedupe by event_id, ordered chronologically.
    by_id: dict[str, dict] = {e["event_id"]: e for e in decision_audits + thread_audits}
    timeline = sorted(by_id.values(), key=lambda e: e["at"])
    return TEMPLATES.TemplateResponse(
        request,
        "admin/order_detail.html",
        _ctx(
            request,
            decision=decision,
            payload=decision.get("payload", {}),
            timeline=timeline,
            page_title=f"Admin — order {decision_id}",
        ),
    )


@router.get("/decisions", response_class=HTMLResponse)
async def admin_decisions(request: Request) -> HTMLResponse:
    pending = decision_list_pending(limit=50)
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for d in pending:
        by_kind[d["kind"]].append(d)
    return TEMPLATES.TemplateResponse(
        request,
        "admin/decisions.html",
        _ctx(
            request,
            pending=pending,
            by_kind=by_kind,
            kinds=["intake", "custom", "care", "marketing"],
            page_title="Admin — pending decisions",
        ),
    )


@router.get("/kitchen", response_class=HTMLResponse)
async def admin_kitchen(request: Request) -> HTMLResponse:
    summary, tickets = await asyncio.gather(
        _kitchen_summary_safe(),
        _kitchen_tickets_safe(),
    )
    by_status: dict[str, list[dict]] = defaultdict(list)
    for t in tickets:
        by_status[(t.get("status") or "unknown").lower()].append(t)
    return TEMPLATES.TemplateResponse(
        request,
        "admin/kitchen.html",
        _ctx(
            request,
            summary=summary,
            tickets=tickets,
            by_status=by_status,
            mcp_configured=hosted_mcp().is_configured(),
            page_title="Admin — kitchen",
        ),
    )


@router.get("/inventory", response_class=HTMLResponse)
async def admin_inventory(request: Request) -> HTMLResponse:
    cakes = catalog_mcp.list_all()
    today = date.today()
    days = [today + timedelta(days=i) for i in range(7)]
    rows = []
    for cake in cakes:
        if cake.slug == "custom":
            continue
        days_avail = []
        for d in days:
            avail = inventory_mcp.available(cake.slug, d)
            days_avail.append({
                "date": d.isoformat(),
                "remaining": avail.get("remaining", 0),
                "capacity": avail.get("capacity", 0),
                "available": avail.get("available", False),
            })
        rows.append({"cake": cake, "days": days_avail})
    return TEMPLATES.TemplateResponse(
        request,
        "admin/inventory.html",
        _ctx(
            request,
            rows=rows,
            day_labels=[d.strftime("%a %m/%d") for d in days],
            page_title="Admin — inventory",
        ),
    )


@router.get("/sales", response_class=HTMLResponse)
async def admin_sales(request: Request) -> HTMLResponse:
    history, margins, recent_orders = await asyncio.gather(
        _marketing_sales_history_safe(),
        _marketing_margin_by_product_safe(),
        _square_recent_orders_safe(50),
    )
    # Channel mix from local audit (dispatcher records channel on every turn).
    audits = audit_recent(limit=1000)
    channels: Counter[str] = Counter()
    for e in audits:
        if e["kind"] == "message_inbound":
            ch = (e.get("payload") or {}).get("channel")
            if ch:
                channels[ch] += 1
    return TEMPLATES.TemplateResponse(
        request,
        "admin/sales.html",
        _ctx(
            request,
            history=history,
            margins=margins,
            recent_orders=recent_orders,
            channels=channels.most_common(),
            mcp_configured=hosted_mcp().is_configured(),
            page_title="Admin — sales",
        ),
    )


@router.get("/marketing", response_class=HTMLResponse)
async def admin_marketing(request: Request) -> HTMLResponse:
    defaults = marketing_mcp.channel_defaults()
    # The hardcoded $500 plan from the README hypothesis. Stays in sync with the
    # business analyst section without re-running the notebook here.
    plan = [
        {"channel": "Meta Ads",          "budget_usd": 100},
        {"channel": "Google Ads",        "budget_usd":  50},
        {"channel": "Boosted IG posts",  "budget_usd":  50},
        {"channel": "Review generation", "budget_usd":  30},
        {"channel": "Retention SMS",     "budget_usd":  20},
    ]
    h = hosted_mcp()
    campaigns: list[dict] = []
    if h.is_configured():
        try:
            res = await h.call_tool("marketing_get_campaign_metrics")
            if isinstance(res, dict):
                campaigns = res.get("campaigns") or res.get("items") or []
            elif isinstance(res, list):
                campaigns = res
        except MCPError as exc:
            log.info("marketing_get_campaign_metrics unavailable: %s", exc)

    # Closed-loop activity surfaced from the local audit table. Empty
    # gracefully when the loop hasn't run yet.
    recent_routes = _recent_audit_safe("marketing_lead_routed", limit=20)
    recent_adjustments = _recent_audit_safe("marketing_campaign_adjusted", limit=10)
    pending_drafts = _pending_marketing_drafts_safe()

    return TEMPLATES.TemplateResponse(
        request,
        "admin/marketing.html",
        _ctx(
            request,
            channel_defaults=defaults,
            plan=plan,
            total_plan_usd=sum(p["budget_usd"] for p in plan),
            campaigns=campaigns,
            recent_routes=recent_routes,
            recent_adjustments=recent_adjustments,
            pending_drafts=pending_drafts,
            mcp_configured=h.is_configured(),
            page_title="Admin — marketing",
        ),
    )


@router.get("/audit", response_class=HTMLResponse)
async def admin_audit(request: Request, kind: str | None = None,
                      limit: int = 100) -> HTMLResponse:
    events = audit_recent(kind=kind, limit=limit)
    all_kinds = sorted({e["kind"] for e in audit_recent(limit=1000)})
    return TEMPLATES.TemplateResponse(
        request,
        "admin/audit.html",
        _ctx(
            request,
            events=events,
            kind=kind,
            all_kinds=all_kinds,
            limit=limit,
            page_title="Admin — audit log",
        ),
    )
