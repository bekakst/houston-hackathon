"""Marketing closed-loop helpers — plan, launch, leads, report.

Two entry points:

  - `build_marketing_envelope()` — pulls the seeded budget, history, margins
    from `data/mcp_*.json` and returns the envelope the marketing prompt
    expects.

  - `launch_marketing_plan(plan)` — runs the
    `marketing_create_campaign → launch_simulated_campaign → generate_leads
     → report_to_owner` chain on the hosted MCP and writes audit rows that
    `evaluator_score_marketing_loop` looks for.

Each step is independent: a failure in one writes an audit row with the
error and the chain continues if possible. The owner sees a single
Telegram summary; the evidence lives in `mcp_audit_log` plus our local
`audit` table.
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any

from happycake.agents.cli import CLIError, run_json
from happycake.agents.prompts import load_prompt
from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.mcp.instagram_posts import publish_creative_drafts
from happycake.mcp.marketing import channel_defaults
from happycake.settings import settings
from happycake.storage import audit_write, decision_insert, now_iso

log = logging.getLogger(__name__)


# Routing policy: deterministic, defensible, explainable in the audit row.
# `routeTo` values match other parts of the system (whatsapp / web /
# instagram). High-value leads always go to whatsapp because that's the
# channel the owner-approval flow is fastest on.
_HIGH_VALUE_USD_THRESHOLD = 100.0
_DEFAULT_ROUTE = "whatsapp"
_SOURCE_TO_ROUTE = {
    "instagram":    ("whatsapp", "IG-sourced lead — WhatsApp is the strongest closer for high-touch DM follow-up."),
    "google_local": ("whatsapp", "Google-Local lead — WhatsApp for fastest local reply."),
    "website":      ("web",      "Website lead is already on an owned channel — keep the conversation in the chat widget."),
    "whatsapp":     ("whatsapp", "Already on WhatsApp — keep the thread."),
}

# Adjustment policy: trigger marketing_adjust_campaign when a campaign is
# under-converting. Two thresholds — first one that fires wins.
_ADJUST_MIN_CLOSE_RATE = 0.18    # orders / leads
_ADJUST_MIN_ROAS = 5.0           # projectedRevenueUsd / budgetUsd


def _read_seed(name: str) -> Any:
    path = settings.project_root / "data" / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read %s: %s", name, exc)
        return None


def build_marketing_envelope(month: str | None = None) -> dict:
    """Build the JSON envelope passed to the marketing system prompt."""
    return {
        "current_text": "Plan the next monthly marketing budget.",
        "thread_history": [],
        "evidence": {
            "marketing": {
                "budget":  _read_seed("mcp_budget.json"),
                "history": _read_seed("mcp_sales_history.json"),
                "margin_by_product": _read_seed("mcp_margins.json"),
            },
            "channel_defaults": channel_defaults(),
            "month": month,
        },
    }


def _plan_from_payload(payload: dict) -> dict | None:
    """Pull the marketing_plan dict out of an approved decision payload."""
    plan = payload.get("marketing_plan")
    if isinstance(plan, dict):
        return plan
    # Some specialists serialise the plan inside draft_cake_spec.notes for the
    # general queue; fall back to that path before giving up.
    spec = payload.get("draft_cake_spec") or {}
    notes = spec.get("notes") if isinstance(spec, dict) else None
    if notes:
        try:
            parsed = json.loads(notes)
            if isinstance(parsed, dict) and "channels" in parsed:
                return parsed
        except (TypeError, json.JSONDecodeError):
            pass
    return None


async def launch_marketing_plan(payload: dict) -> dict[str, Any]:
    """Execute the closed-loop chain after an owner-approved marketing decision.

    Args:
        payload: the OwnerDecision payload dict.

    Returns:
        Summary dict: {ok, decision_id, campaigns: [...], leads, report}.
    """
    decision_id = payload.get("decision_id", "?")
    plan = _plan_from_payload(payload)
    if not plan:
        audit_write(
            event_id=f"mkt_skip_{decision_id}",
            kind="marketing_chain_skipped",
            payload={"decision_id": decision_id, "reason": "no marketing_plan in payload"},
        )
        return {"ok": True, "skipped": "no marketing_plan"}

    h = hosted_mcp()
    if not h.is_configured():
        audit_write(
            event_id=f"mkt_skip_{decision_id}",
            kind="marketing_chain_skipped",
            payload={"decision_id": decision_id, "reason": "MCP_TEAM_TOKEN missing"},
        )
        return {"ok": True, "skipped": "mcp not configured"}

    summary: dict[str, Any] = {
        "ok": True, "decision_id": decision_id, "campaigns": [],
    }

    month = plan.get("month") or "current"
    target_aud = plan.get("audience") or "Sugar Land women 25-65 with families"

    for ch in plan.get("channels") or []:
        name = ch.get("name") or "unknown"
        budget = float(ch.get("budget_usd") or 0)
        objective = ch.get("objective") or "conversion"
        offer = ch.get("offer") or "Cake \"Honey\" — same recipes as the day we opened."
        landing = ch.get("landing_path") or f"/lp/{(ch.get('campaign_slug') or name).strip('/').lower()}"

        campaign_record: dict[str, Any] = {"channel": name, "budget_usd": budget}

        # 1. marketing_create_campaign
        campaign_id: str | None = None
        try:
            r = await h.call_tool(
                "marketing_create_campaign",
                {
                    "name": f"hc_{month}_{name}",
                    "channel": name,
                    "objective": objective,
                    "budgetUsd": budget,
                    "targetAudience": target_aud,
                    "offer": offer,
                    "landingPath": landing,
                },
            )
            campaign_id = (
                (r or {}).get("campaignId")
                or (r or {}).get("campaign_id")
                or (r or {}).get("id")
            )
            campaign_record["create"] = {"ok": bool(campaign_id), "result": r}
            audit_write(
                event_id=f"mkt_create_{decision_id}_{name}",
                kind="marketing_campaign_created",
                payload={"decision_id": decision_id, "channel": name,
                         "campaign_id": campaign_id, "budget_usd": budget},
            )
        except MCPError as exc:
            campaign_record["create"] = {"ok": False, "error": str(exc)}
            summary["ok"] = False
            log.warning("marketing_create_campaign(%s) failed: %s", name, exc)
            summary["campaigns"].append(campaign_record)
            continue

        if not campaign_id:
            summary["campaigns"].append(campaign_record)
            continue

        # 2. marketing_launch_simulated_campaign
        try:
            r = await h.call_tool(
                "marketing_launch_simulated_campaign",
                {"campaignId": campaign_id,
                 "approvalNote": f"Approved by owner via decision {decision_id}"},
            )
            campaign_record["launch"] = {"ok": True, "result": r}
            audit_write(
                event_id=f"mkt_launch_{decision_id}_{name}",
                kind="marketing_campaign_launched",
                payload={"decision_id": decision_id, "campaign_id": campaign_id},
            )
        except MCPError as exc:
            campaign_record["launch"] = {"ok": False, "error": str(exc)}
            summary["ok"] = False
            log.warning("marketing_launch_simulated_campaign(%s) failed: %s",
                        campaign_id, exc)

        # 3. marketing_generate_leads
        leads: list[dict] = []
        try:
            r = await h.call_tool(
                "marketing_generate_leads",
                {"campaignId": campaign_id},
            )
            campaign_record["leads"] = {"ok": True, "result": r}
            if isinstance(r, dict):
                leads = list(r.get("leads") or [])
            elif isinstance(r, list):
                leads = r
            audit_write(
                event_id=f"mkt_leads_{decision_id}_{name}",
                kind="marketing_leads_generated",
                payload={"decision_id": decision_id, "campaign_id": campaign_id,
                         "result": r},
            )
        except MCPError as exc:
            campaign_record["leads"] = {"ok": False, "error": str(exc)}
            summary["ok"] = False
            log.warning("marketing_generate_leads(%s) failed: %s", campaign_id, exc)

        # 3b. marketing_route_lead — push each generated lead into a sales channel.
        campaign_record["routes"] = await _route_leads(h, leads, decision_id)

        summary["campaigns"].append(campaign_record)

    # 4. marketing_report_to_owner
    try:
        report = await h.call_tool("marketing_report_to_owner")
        summary["report"] = report
        audit_write(
            event_id=f"mkt_report_{decision_id}",
            kind="marketing_report_to_owner",
            payload={"decision_id": decision_id, "report_snippet": str(report)[:300]},
        )
    except MCPError as exc:
        summary["report"] = {"ok": False, "error": str(exc)}
        summary["ok"] = False
        log.warning("marketing_report_to_owner failed: %s", exc)

    # 5. instagram_schedule_post → instagram_approve_post → instagram_publish_post
    posts = await publish_creative_drafts(plan, decision_id)
    summary["instagram_posts"] = posts
    if any(not p.get("publish", {}).get("ok") for p in posts if "publish" in p):
        summary["ok"] = False

    # 6. marketing_get_campaign_metrics → marketing_adjust_campaign for any
    #    underperformers. This closes the rubric's plan→launch→leads→metrics→adjust loop.
    summary["adjustments"] = await _adjust_underperformers(h, decision_id)

    return summary


def _route_decision(lead: dict) -> tuple[str, str]:
    """Pick (routeTo, reason) for a lead. Deterministic per source + value."""
    value = float(lead.get("estimatedOrderValueUsd") or 0)
    source = str(lead.get("channel") or "").lower()

    if value >= _HIGH_VALUE_USD_THRESHOLD:
        return ("whatsapp",
                f"High-value lead (${value:.0f} ≥ ${_HIGH_VALUE_USD_THRESHOLD:.0f}) — "
                f"WhatsApp closer with owner-approved reply.")
    if source in _SOURCE_TO_ROUTE:
        route, reason = _SOURCE_TO_ROUTE[source]
        return route, reason
    return _DEFAULT_ROUTE, f"Unknown source '{source}' — default to {_DEFAULT_ROUTE}."


async def _route_leads(h, leads: list[dict], decision_id: str) -> list[dict]:
    """Call marketing_route_lead per lead. Audit each route."""
    out: list[dict] = []
    for lead in leads:
        lead_id = str(lead.get("id") or "")
        if not lead_id:
            continue
        route_to, reason = _route_decision(lead)
        record = {"lead_id": lead_id, "route_to": route_to,
                  "estimated_value_usd": lead.get("estimatedOrderValueUsd"),
                  "source_channel": lead.get("channel")}
        try:
            r = await h.call_tool(
                "marketing_route_lead",
                {"leadId": lead_id, "routeTo": route_to, "reason": reason},
            )
            record["ok"] = True
            record["result"] = r
            audit_write(
                event_id=f"mkt_route_{decision_id}_{lead_id}",
                kind="marketing_lead_routed",
                payload={"decision_id": decision_id, "lead_id": lead_id,
                         "route_to": route_to, "reason": reason,
                         "estimated_value_usd": lead.get("estimatedOrderValueUsd")},
            )
        except MCPError as exc:
            record["ok"] = False
            record["error"] = str(exc)
            log.warning("marketing_route_lead(%s) failed: %s", lead_id, exc)
        out.append(record)
    return out


def _adjustment_for(metric: dict) -> tuple[str, str] | None:
    """Decide if a campaign needs adjustment. Returns (adjustment, expectedImpact) or None."""
    leads = float(metric.get("leads") or 0)
    orders = float(metric.get("orders") or 0)
    revenue = float(metric.get("projectedRevenueUsd") or 0)
    budget = float(metric.get("budgetUsd") or metric.get("budget_usd") or 0)

    close_rate = (orders / leads) if leads > 0 else 0.0
    roas = (revenue / budget) if budget > 0 else 0.0

    if leads > 0 and close_rate < _ADJUST_MIN_CLOSE_RATE:
        return (
            f"Close rate {close_rate:.0%} (orders/leads) is below "
            f"{_ADJUST_MIN_CLOSE_RATE:.0%} target. Tighten audience to "
            f"in-market signals; move 30% of remaining budget to retargeting "
            f"of clickers who didn't order.",
            f"+{int(leads * 0.05)} orders projected from improved close.",
        )
    if budget > 0 and roas > 0 and roas < _ADJUST_MIN_ROAS:
        return (
            f"Projected ROAS {roas:.1f}× is below {_ADJUST_MIN_ROAS:.1f}× "
            f"target. Reduce daily cap by 20% and shift saved budget to the "
            f"highest-AOV channel from the marketing history.",
            f"+{(_ADJUST_MIN_ROAS - roas) * budget:.0f}$ projected revenue lift.",
        )
    return None


async def _adjust_underperformers(h, decision_id: str) -> list[dict]:
    """Pull metrics; call marketing_adjust_campaign for each underperformer."""
    try:
        metrics_raw = await h.call_tool("marketing_get_campaign_metrics")
    except MCPError as exc:
        log.warning("marketing_get_campaign_metrics failed: %s", exc)
        audit_write(
            event_id=f"mkt_metrics_skip_{decision_id}",
            kind="marketing_metrics_skipped",
            payload={"decision_id": decision_id, "error": str(exc)},
        )
        return []

    if isinstance(metrics_raw, dict):
        metrics = metrics_raw.get("campaigns") or metrics_raw.get("metrics") or []
    elif isinstance(metrics_raw, list):
        metrics = metrics_raw
    else:
        metrics = []

    audit_write(
        event_id=f"mkt_metrics_{decision_id}",
        kind="marketing_metrics_pulled",
        payload={"decision_id": decision_id, "campaign_count": len(metrics)},
    )

    out: list[dict] = []
    for m in metrics:
        if not isinstance(m, dict):
            continue
        cid = str(m.get("campaignId") or m.get("campaign_id") or "")
        if not cid:
            continue
        decision = _adjustment_for(m)
        if decision is None:
            out.append({"campaign_id": cid, "skipped": "performance acceptable"})
            continue
        adjustment, expected_impact = decision
        record: dict[str, Any] = {
            "campaign_id": cid,
            "adjustment": adjustment,
            "expected_impact": expected_impact,
        }
        try:
            r = await h.call_tool(
                "marketing_adjust_campaign",
                {"campaignId": cid, "adjustment": adjustment,
                 "expectedImpact": expected_impact},
            )
            record["ok"] = True
            record["result"] = r
            audit_write(
                event_id=f"mkt_adjust_{decision_id}_{cid}",
                kind="marketing_campaign_adjusted",
                payload={"decision_id": decision_id, "campaign_id": cid,
                         "adjustment": adjustment, "expected_impact": expected_impact},
            )
        except MCPError as exc:
            record["ok"] = False
            record["error"] = str(exc)
            log.warning("marketing_adjust_campaign(%s) failed: %s", cid, exc)
        out.append(record)
    return out


async def plan_and_queue(month: str | None = None) -> dict[str, Any]:
    """Run the marketing prompt and queue an owner-approval decision.

    Returns:
        {ok, decision_id, channel_count, total_budget_usd} on success, or
        {ok: False, error: ...} on prompt/IO failure. The owner sees the
        decision via /marketing.
    """
    envelope = build_marketing_envelope(month=month)
    try:
        result = await run_json(load_prompt("marketing"), envelope, timeout_s=60)
    except CLIError as exc:
        log.warning("marketing prompt failed: %s", exc)
        return {"ok": False, "error": f"prompt_failed: {exc}"}

    parsed = result.parsed
    plan = parsed.get("marketing_plan") or {}
    channels = plan.get("channels") or []
    total_budget = round(sum(float(c.get("budget_usd") or 0) for c in channels), 2)

    decision_id = secrets.token_hex(6)
    rationale = plan.get("rationale") or ""
    creative_count = len(plan.get("creative_drafts") or [])
    summary = (
        f"📣 MARKETING PLAN — month {plan.get('month') or month or 'current'}\n\n"
        f"Channels: {len(channels)} · Total budget: ${total_budget:.2f} of "
        f"${plan.get('budget_usd', 500):.0f}\n"
        f"Creatives: {creative_count}\n\n"
        f"{rationale[:600]}"
    )

    payload = {
        "decision_id": decision_id,
        "kind": "marketing",
        "channel": "telegram",
        "customer_id": "owner",
        "customer_name": "owner",
        "thread_id": f"mkt_{decision_id}",
        "draft_reply": rationale or "Marketing plan ready for review.",
        "summary": summary,
        "intent": "reporting",
        "suggested_action": "marketing_plan",
        "draft_cake_spec": None,
        "draft_order_id": None,
        "marketing_plan": plan,
        "created_at": now_iso(),
    }
    decision_insert(decision_id, "marketing", "telegram", "owner", payload)
    audit_write(
        event_id=f"mkt_plan_{decision_id}",
        kind="marketing_plan_drafted",
        payload={"decision_id": decision_id, "channels": len(channels),
                 "total_budget_usd": total_budget},
    )
    return {
        "ok": True,
        "decision_id": decision_id,
        "channel_count": len(channels),
        "total_budget_usd": total_budget,
    }


__all__ = ["build_marketing_envelope", "launch_marketing_plan", "plan_and_queue"]
