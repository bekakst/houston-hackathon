"""Specialist agents — intake, custom, care, marketing, reporting.

Each is a thin async function that:
  1. Pre-fetches grounded MCP/local facts for the customer turn.
  2. Builds the JSON envelope (turn + evidence + history).
  3. Calls run_json against the matching ops/prompts/*.md system prompt.
  4. Returns a typed Reply object.

Failure modes always degrade to a templated escalation reply — never to
fabrication.
"""

from __future__ import annotations

import logging
from typing import Literal

from happycake.agents.cli import CLIError, run_json
from happycake.agents.grounding import ground_for_intent
from happycake.agents.prompts import load_prompt
from happycake.schemas import CakeSpec, Evidence, Intent, Reply

log = logging.getLogger(__name__)

STANDARD_CLOSE = "Order on the site at happycake.us or send a message on WhatsApp."

_ESCALATION_REPLY = (
    "Thank you for the message. A team member will review and reply shortly "
    "with a clear answer. " + STANDARD_CLOSE
)


def _to_reply(parsed: dict, *, intent: Intent) -> Reply:
    """Coerce the LLM JSON into a typed Reply."""
    spec_dict = parsed.get("draft_cake_spec")
    spec_obj: CakeSpec | None = None
    if isinstance(spec_dict, dict):
        try:
            spec_obj = CakeSpec.model_validate(spec_dict)
        except Exception as exc:  # noqa: BLE001
            log.warning("draft_cake_spec validation failed: %s", exc)

    return Reply(
        reply_to_customer=str(parsed.get("reply_to_customer") or "").strip()
            or _ESCALATION_REPLY,
        needs_owner_approval=bool(parsed.get("needs_owner_approval", False)),
        suggested_action=parsed.get("suggested_action"),
        draft_order_id=parsed.get("draft_order_id"),
        draft_cake_spec=spec_obj,
        evidence=[],  # the dispatcher fills evidence after MCP grounding
        intent=intent,
    )


def _fallback_reply(intent: Intent, reason: str) -> Reply:
    log.info("specialist fallback: intent=%s reason=%s", intent.value, reason)
    return Reply(
        reply_to_customer=_ESCALATION_REPLY,
        needs_owner_approval=True,
        suggested_action=f"escalate:{reason}",
        intent=intent,
    )


async def _run_specialist(prompt_name: str, intent: Intent,
                          envelope: dict, *, timeout_s: int | None = None) -> Reply:
    try:
        result = await run_json(load_prompt(prompt_name), envelope, timeout_s=timeout_s)
    except CLIError as exc:
        log.warning("%s CLI error: %s", prompt_name, exc)
        return _fallback_reply(intent, "agent_unreachable")
    return _to_reply(result.parsed, intent=intent)


def _ensure_spec_from_grounding(reply: Reply, evidence: dict, *,
                                key: str = "intake") -> Reply:
    """Safety net: when the LLM sets needs_owner_approval but forgets the
    draft_cake_spec, synthesise one from the grounded heuristic detection so
    the POS + kitchen chain has something to work with.

    The synthesised spec is intentionally minimal — base_cake_slug,
    size_label, and fulfillment. The downstream fulfillment helper only
    requires the first two; everything else is best-effort.
    """
    if not reply.needs_owner_approval or reply.draft_cake_spec is not None:
        return reply
    detected = ((evidence or {}).get(key) or {}).get("detected") or {}
    slug = detected.get("cake_slug")
    size = detected.get("size_label")
    if not (slug and size):
        return reply
    try:
        reply.draft_cake_spec = CakeSpec(
            base_cake_slug=slug,
            size_label=size,
            fulfillment=detected.get("fulfillment") or "pickup",
        )
        log.info("synthesized draft_cake_spec from grounding: %s/%s/%s",
                 slug, size, detected.get("fulfillment") or "pickup")
    except Exception as exc:  # noqa: BLE001
        log.warning("draft_cake_spec synth failed: %s", exc)
    return reply


async def run_intake(text: str, *, history: list[dict] | None = None) -> Reply:
    evidence = ground_for_intent("intake", text)
    envelope = {
        "current_text": text,
        "thread_history": (history or [])[-6:],
        "evidence": evidence,
    }
    reply = await _run_specialist("intake", Intent.intake, envelope)
    return _ensure_spec_from_grounding(reply, evidence, key="intake")


async def run_custom(text: str, *, history: list[dict] | None = None,
                     partial_spec: dict | None = None) -> Reply:
    evidence = ground_for_intent("custom", text, partial_spec=partial_spec)
    envelope = {
        "current_text": text,
        "thread_history": (history or [])[-6:],
        "cake_spec_so_far": partial_spec or {},
        "evidence": evidence,
    }
    reply = await _run_specialist("custom", Intent.custom, envelope)
    return _ensure_spec_from_grounding(reply, evidence, key="custom")


async def run_care(text: str, *, history: list[dict] | None = None,
                   verified: bool = False) -> Reply:
    evidence = ground_for_intent("care", text, verified=verified)
    envelope = {
        "current_text": text,
        "thread_history": (history or [])[-6:],
        "verified": verified,
        "evidence": evidence,
    }
    return await _run_specialist("care", Intent.care, envelope)


async def run_reporting(envelope: dict) -> Reply:
    """Reporting takes a pre-built evidence envelope (from the owner bot)."""
    return await _run_specialist("reporting", Intent.reporting, envelope, timeout_s=60)


async def run_marketing(envelope: dict) -> Reply:
    """Marketing takes a pre-built evidence envelope (budget + history + margins)."""
    return await _run_specialist("marketing", Intent.reporting, envelope, timeout_s=60)
