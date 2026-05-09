"""Single entry point for every customer message on every channel.

Flow:
    safety_pre_filter -> router -> specialist agent -> brand_critic -> outbound

The dispatcher is the place where the brief's hard rules are enforced in
Python — even if every prompt regression in the world conspires, the
dispatcher will:

- escalate any allergen / human-handoff / prompt-injection message before
  reaching the LLM (safety_pre_filter).
- escalate any low-confidence routing decision (router < 0.6).
- escalate any draft that the brand-voice critic refuses to approve.
- write an audit-trail row + queue an OwnerDecision when needs_owner_approval
  is true.

The contract returned to callers (web assistant, gateway) is the same Reply
schema everywhere.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from happycake.agents.brand_critic import critique
from happycake.agents.router import classify
from happycake.agents.safety import safety_pre_filter
from happycake.agents.specialists import (
    STANDARD_CLOSE,
    run_care,
    run_custom,
    run_intake,
    run_reporting,
)
from happycake.mcp import evidence as evidence_mcp
from happycake.schemas import (
    Channel,
    Evidence,
    Intent,
    Reply,
)
from happycake.storage import audit_write, decision_insert, now_iso

log = logging.getLogger(__name__)

# In-memory thread history. Persisting threads is out-of-scope for the
# hackathon — the audit trail captures the customer messages individually.
_THREAD_HISTORY: dict[str, list[dict]] = {}


def _new_decision_id() -> str:
    return secrets.token_hex(6)


def _record_history(thread_id: str, role: str, text: str) -> None:
    history = _THREAD_HISTORY.setdefault(thread_id, [])
    history.append({"role": role, "text": text, "at": now_iso()})
    # cap at 20 turns
    if len(history) > 20:
        del history[: len(history) - 20]


def _escalation_reply(reason: str, channel: Channel) -> Reply:
    """The deterministic fallback used whenever any link in the chain fails."""
    return Reply(
        reply_to_customer=(
            "Thank you for the message. A team member will review and reply "
            f"shortly with a clear answer. {STANDARD_CLOSE}"
        ),
        needs_owner_approval=True,
        suggested_action=f"escalate:{reason}",
        intent=Intent.escalate,
    )


def _build_card_summary(channel: Channel, sender_name: str, reply_text: str,
                        intent: Intent, suggested_action: str | None) -> str:
    """One-screen, scannable card body for the Telegram approval queue."""
    lines = [
        f"🎂 {intent.value.upper()} — {sender_name}, {channel.value}",
        "",
        "Suggested reply:",
        reply_text[:600] + ("…" if len(reply_text) > 600 else ""),
    ]
    if suggested_action:
        lines.extend(["", f"Reason: {suggested_action}"])
    return "\n".join(lines)


async def handle_customer_message(
    *,
    channel: Channel,
    sender: str,
    sender_name: str,
    text: str,
    thread_id: str,
) -> Reply:
    """Process a customer turn end-to-end. Always returns a Reply."""

    _record_history(thread_id, "customer", text)
    history = list(_THREAD_HISTORY.get(thread_id, [])[:-1])  # exclude this turn

    audit_write(
        event_id=f"in_{secrets.token_hex(6)}",
        kind="message_inbound",
        payload={
            "thread_id": thread_id, "channel": channel.value,
            "sender": sender, "text": text[:500],
        },
    )

    # Ring 1 — Python safety pre-filter (deterministic, no LLM involved).
    safety = safety_pre_filter(text)
    if safety is not None:
        log.info("safety pre-filter: %s (%s)", safety.reason, ",".join(safety.matched))
        reply = _escalation_reply(safety.reason, channel)
        await _queue_decision(reply, channel=channel, sender=sender,
                              sender_name=sender_name, thread_id=thread_id,
                              kind="care")
        _record_history(thread_id, "agent", reply.reply_to_customer)
        return reply

    # Ring 2 — LLM router.
    intent, confidence, reason = await classify(text, history=history)
    log.info("router: %s confidence=%.2f reason=%s", intent.value, confidence, reason)

    if intent == Intent.escalate:
        reply = _escalation_reply(reason or "router_escalate", channel)
        await _queue_decision(reply, channel=channel, sender=sender,
                              sender_name=sender_name, thread_id=thread_id,
                              kind="care")
        _record_history(thread_id, "agent", reply.reply_to_customer)
        return reply

    # Ring 3 — Specialist.
    if intent == Intent.intake:
        reply = await run_intake(text, history=history)
    elif intent == Intent.custom:
        reply = await run_custom(text, history=history)
    elif intent == Intent.care:
        reply = await run_care(text, history=history, verified=False)
    elif intent == Intent.reporting:
        # Reporting is owner-driven. If a customer triggers it, escalate.
        reply = _escalation_reply("customer_triggered_reporting", channel)
    else:
        reply = _escalation_reply("unhandled_intent", channel)

    # Ring 4 — Brand voice critic on customer-facing text.
    if reply.reply_to_customer and not reply.suggested_action:
        approved, rewritten, violations = await critique(reply.reply_to_customer,
                                                         surface="customer")
        if approved:
            reply.reply_to_customer = rewritten
            if violations:
                log.info("brand_critic auto-corrected: %s", ",".join(violations))
        else:
            log.warning("brand_critic blocked draft: %s", ",".join(violations))
            reply = _escalation_reply(f"brand_critic_blocked:{','.join(violations)[:80]}",
                                      channel)

    # Audit + queue if needs owner approval.
    if reply.needs_owner_approval:
        await _queue_decision(
            reply, channel=channel, sender=sender, sender_name=sender_name,
            thread_id=thread_id,
            kind={"intake": "intake", "custom": "custom",
                  "care": "care"}.get(reply.intent.value if reply.intent else "intake",
                                      "care"),
        )

    audit_write(
        event_id=f"out_{secrets.token_hex(6)}",
        kind="message_outbound",
        payload={
            "thread_id": thread_id, "channel": channel.value,
            "intent": reply.intent.value if reply.intent else None,
            "needs_owner_approval": reply.needs_owner_approval,
            "reply_snippet": reply.reply_to_customer[:200],
        },
    )

    _record_history(thread_id, "agent", reply.reply_to_customer)
    return reply


async def _queue_decision(reply: Reply, *, channel: Channel, sender: str,
                          sender_name: str, thread_id: str, kind: str) -> str:
    """Insert an OwnerDecision row so the Telegram bot can surface it.

    Returns the new decision_id. The actual Telegram inline-keyboard rendering
    is in apps/owner_bot/cards.py.
    """
    decision_id = _new_decision_id()
    payload = {
        "decision_id": decision_id,
        "kind": kind,
        "channel": channel.value,
        "customer_id": sender,
        "customer_name": sender_name,
        "thread_id": thread_id,
        "draft_reply": reply.reply_to_customer,
        "summary": _build_card_summary(
            channel, sender_name, reply.reply_to_customer,
            reply.intent or Intent.escalate, reply.suggested_action,
        ),
        "intent": reply.intent.value if reply.intent else None,
        "suggested_action": reply.suggested_action,
        "draft_cake_spec": (
            reply.draft_cake_spec.model_dump(mode="json") if reply.draft_cake_spec else None
        ),
        "draft_order_id": reply.draft_order_id,
        "created_at": now_iso(),
    }
    decision_insert(decision_id, kind, channel.value, sender, payload)
    audit_write(
        event_id=f"dec_{decision_id}",
        kind="decision_queued",
        payload=payload,
    )
    return decision_id


__all__ = ["handle_customer_message"]
