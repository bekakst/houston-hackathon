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
from happycake.mcp.marketing import channel_defaults
from happycake.settings import settings
from happycake.storage import audit_write, decision_insert, now_iso

log = logging.getLogger(__name__)


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
        try:
            r = await h.call_tool(
                "marketing_generate_leads",
                {"campaignId": campaign_id},
            )
            campaign_record["leads"] = {"ok": True, "result": r}
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

    return summary


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
