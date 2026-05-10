"""Telegram bot handlers — commands + callback queries.

Hard rule from HACKATHON_BRIEF.md §4: Telegram is the ONLY owner-facing UI.
Every action the owner takes (approve, reject, edit, reports) is a tap on an
inline keyboard. No free-text "type the reason" prompts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from apps.owner_bot.cards import (
    KITCHEN_REJECT_REASONS,
    REJECT_REASONS,
    approval_keyboard,
    build_card_text,
    build_kitchen_card,
    kitchen_keyboard,
    kitchen_reject_reason_keyboard,
    main_menu_keyboard,
    reject_reason_keyboard,
    report_period_keyboard,
    sent_keyboard,
)
from apps.owner_bot.outbound import send_to_customer
from happycake.mcp import orders as orders_mcp
from happycake.mcp.fulfillment import fulfill_approved
from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.mcp.marketing_loop import launch_marketing_plan, plan_and_queue
from happycake.settings import settings
from happycake.storage import (
    audit_recent,
    audit_write,
    decision_get,
    decision_list_pending,
    decision_set_status,
    now_iso,
)

log = logging.getLogger(__name__)


def _owner_chat_id() -> int | None:
    raw = settings.telegram_owner_chat_id
    try:
        cid = int(raw)
    except (TypeError, ValueError):
        return None
    return cid if cid > 0 else None


def _is_owner(update: Update) -> bool:
    """True if the message comes from the configured owner chat.

    Returns True when TELEGRAM_OWNER_CHAT_ID is unset/0 so dev setups still
    work — the operator uses /whoami first to learn their id, then sets it.
    """
    expected = _owner_chat_id()
    if expected is None:
        return True
    chat = update.effective_chat
    return chat is not None and chat.id == expected


async def _reject_non_owner(update: Update) -> None:
    if update.message:
        await update.message.reply_text(
            "This bot only answers the configured HappyCake owner. "
            "Use /whoami to share your chat id with the team."
        )
    elif update.callback_query:
        await update.callback_query.answer(
            "Only the owner can approve decisions.", show_alert=True,
        )


def _format_pending_count(decisions: list[dict]) -> str:
    by_kind: dict[str, int] = {}
    for d in decisions:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
    if not by_kind:
        return "No pending decisions."
    lines = [f"  {k}: {v}" for k, v in sorted(by_kind.items())]
    return "Pending decisions by kind:\n" + "\n".join(lines)


# ── Commands ────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    user = update.effective_user
    text = (
        "Good morning, friends.\n\n"
        f"This is the HappyCake owner bot. Hi {user.first_name if user else ''}.\n\n"
        "I send you one card per pending decision — order, custom request, or "
        "complaint. You tap Approve / Reject / Edit. The customer hears back the "
        "moment you tap.\n\n"
        "Tap a button below to start, or type /help."
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    text = (
        "*HappyCake owner bot — quick reference*\n\n"
        "/orders — pending standard orders\n"
        "/custom — pending custom-cake requests\n"
        "/care — open complaints / status / refunds\n"
        "/reports — today / 7-day / 30-day summary\n"
        "/marketing — review and approve marketing drafts\n"
        "/plan_marketing — draft next month's $500 plan and queue for approval\n"
        "/status `<order_id>` — quick lookup\n"
        "/replay `<thread_id>` — see the agent's reasoning trace\n"
        "/audit — last 20 audit events\n"
        "/kitchen — open kitchen tickets (accept · mark ready · reject)\n"
        "/kitchen\\_summary — production capacity + ticket-status counts\n"
        "/whoami — your chat id (for setup)\n\n"
        "Every approval is a tap. Reject reasons are buttons — never typing."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=main_menu_keyboard())


async def cmd_whoami(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user
    await update.message.reply_text(
        f"chat_id: `{chat_id}`\nuser_id: `{user.id if user else '?'}`\n"
        f"username: @{user.username if user and user.username else '?'}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _send_decision_cards(update: Update, kind: str | None) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    pending = decision_list_pending(kind=kind, limit=10)
    if not pending:
        msg = (f"No pending {kind or ''} decisions. "
               "I'll surface new ones automatically as customers reach out.")
        await update.message.reply_text(msg.strip())
        return
    await update.message.reply_text(
        f"{len(pending)} pending decision(s). Newest first:"
    )
    for d in pending:
        payload = d["payload"]
        decision_id = payload["decision_id"]
        body = build_card_text(payload)
        await update.message.reply_text(
            body, reply_markup=approval_keyboard(decision_id),
        )


async def cmd_orders(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_decision_cards(update, "intake")


async def cmd_custom(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_decision_cards(update, "custom")


async def cmd_care(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_decision_cards(update, "care")


async def cmd_marketing(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_decision_cards(update, "marketing")


async def cmd_plan_marketing(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    await update.message.reply_text(
        "Drafting next month's $500 plan. This calls the marketing agent and "
        "may take 30–60s…"
    )
    result = await plan_and_queue()
    if not result.get("ok"):
        await update.message.reply_text(
            f"⚠ Marketing plan failed: {result.get('error', 'unknown error')}."
        )
        return
    await update.message.reply_text(
        f"📣 Plan queued. Decision id `{result['decision_id']}`. "
        f"Allocates ${result['total_budget_usd']:.0f} across "
        f"{result['channel_count']} channels.\n\n"
        f"Run /marketing to review and Approve.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_reports(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    pending = decision_list_pending(limit=50)
    overview = _format_pending_count(pending)
    await update.message.reply_text(
        f"{overview}\n\nPick a reporting period:",
        reply_markup=report_period_keyboard(),
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /status `<order_id>` — e.g. /status ord_demo_001",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    order_id = args[0]
    record = orders_mcp.get(order_id)
    if not record:
        await update.message.reply_text(f"No order found for `{order_id}`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(
        f"`{order_id}` status: *{record.get('status', '?')}*\n"
        f"Channel: {record.get('channel', '?')}\n"
        f"Total: ${record.get('total_usd', 0):.2f}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_replay(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /replay `<thread_id>` — show the agent's reasoning trace.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    thread_id = args[0]
    events = [
        e for e in audit_recent(limit=200)
        if e["payload"].get("thread_id") == thread_id
    ]
    if not events:
        await update.message.reply_text(f"No audit events for thread `{thread_id}`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    lines = [f"*Replay — thread {thread_id}*", ""]
    for e in events[:15]:
        lines.append(f"• {e['kind']} — {e['payload'].get('intent') or e['kind']}")
        snippet = (e["payload"].get("text") or e["payload"].get("reply_snippet")
                   or "")[:120]
        if snippet:
            lines.append(f"   {snippet}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_kitchen(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    h = hosted_mcp()
    try:
        result = await h.call_tool("kitchen_list_tickets")
    except MCPError as exc:
        await update.message.reply_text(f"⚠ kitchen_list_tickets failed: {exc}")
        return
    tickets = result if isinstance(result, list) else (result or {}).get("tickets") or []
    open_tickets = [t for t in tickets
                    if (t.get("status") or "").lower() in ("queued", "accepted")]
    if not open_tickets:
        await update.message.reply_text(
            "No queued or accepted kitchen tickets right now. Use "
            "/kitchen_summary for the full state."
        )
        return
    open_tickets.sort(key=lambda t: t.get("createdAt", ""), reverse=True)
    await update.message.reply_text(
        f"🎂 {len(open_tickets)} open kitchen ticket(s). Newest first:"
    )
    for t in open_tickets[:8]:
        await update.message.reply_text(
            build_kitchen_card(t),
            reply_markup=kitchen_keyboard(t.get("id", "?"), t.get("status", "?")),
        )


async def cmd_kitchen_summary(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    h = hosted_mcp()
    try:
        s = await h.call_tool("kitchen_get_production_summary") or {}
    except MCPError as exc:
        await update.message.reply_text(f"⚠ kitchen_get_production_summary failed: {exc}")
        return
    by_status = s.get("byStatus") or {}
    lines = [
        "🍰 *Kitchen production summary*",
        "",
        f"Tickets: {s.get('tickets', 0)} total",
        f"  · queued:    {by_status.get('queued', 0)}",
        f"  · accepted:  {by_status.get('accepted', 0)}",
        f"  · ready:     {by_status.get('ready', 0)}",
        f"  · rejected:  {by_status.get('rejected', 0)}",
        "",
        f"Daily prep capacity: {s.get('dailyCapacityMinutes', 0)} min",
        f"Used:                {s.get('usedPrepMinutes', 0)} min",
        f"Remaining:           {s.get('remainingCapacityMinutes', 0)} min",
        "",
        ("⚠ Over capacity — reject incoming"
         if s.get('overCapacity') else "✅ Capacity available"),
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_audit(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    events = audit_recent(limit=20)
    if not events:
        await update.message.reply_text("No audit events yet.")
        return
    lines = ["*Last 20 audit events*", ""]
    for e in events:
        lines.append(f"• {e['kind']} at {e['at'][11:19]}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── Callback queries ────────────────────────────────────────────────────────


async def on_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    if not _is_owner(update):
        await q.answer("Only the owner can approve decisions.", show_alert=True)
        return
    await q.answer()
    parts = q.data.split(":", 2)
    action = parts[0]
    arg1 = parts[1] if len(parts) > 1 else ""
    arg2 = parts[2] if len(parts) > 2 else ""

    if action == "noop":
        return

    if action == "cmd":
        # main-menu shortcut
        target = arg1
        fake_msg_text = {"orders": "/orders", "custom": "/custom", "care": "/care",
                         "reports": "/reports", "marketing": "/marketing",
                         "help": "/help"}.get(target, "/help")
        await q.message.reply_text(f"Use {fake_msg_text} to view.")
        return

    if action == "report":
        # period selector — placeholder until reporting agent is wired in
        await q.message.reply_text(
            f"Reporting for period `{arg1}` — coming via /reports flow.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action in ("kit_accept", "kit_ready", "kit_reject", "kit_rreason"):
        await _handle_kitchen_action(q, action, arg1, arg2)
        return

    decision_id = arg1
    if not decision_id:
        await q.message.reply_text("Could not parse decision id from button.")
        return
    decision = decision_get(decision_id)
    if not decision:
        await q.message.reply_text(f"Decision `{decision_id}` no longer pending.",
                                   parse_mode=ParseMode.MARKDOWN)
        return
    payload = decision["payload"]

    if action == "approve":
        await _handle_approve(q, decision)
    elif action == "reject":
        await q.edit_message_reply_markup(
            reply_markup=reject_reason_keyboard(decision_id),
        )
    elif action == "rreason":
        await _handle_reject_with_reason(q, decision, reason_code=arg2)
    elif action == "edit":
        await q.message.reply_text(
            "✏️ Edit mode: type the corrected reply as a /edit_reply message "
            f"using decision id `{decision_id}` — coming in v0.2.",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif action == "preview":
        await q.message.reply_text(
            "👁 Preview:\n\n" + payload.get("draft_reply", "(no draft)"),
        )
    elif action == "call":
        cust = payload.get("customer_name", "?")
        await q.message.reply_text(
            f"📞 To call {cust}, open the chat thread on the original channel "
            f"({payload.get('channel')}) and tap the phone number."
        )
    elif action == "kitchen":
        await q.message.reply_text(
            "🚨 Kitchen ack — would post to kitchen channel via "
            "kitchen_create_ticket. (Wired in T16-T17.)"
        )
    else:
        await q.message.reply_text(f"Unknown action: {action}")


async def _handle_approve(q, decision: dict) -> None:
    payload = decision["payload"]
    decision_id = payload["decision_id"]
    decision_set_status(decision_id, "approved")
    audit_write(
        event_id=f"dec_app_{decision_id}",
        kind="decision_approved",
        payload={"decision_id": decision_id, "by": "owner"},
    )
    # Send to customer.
    send_result = await send_to_customer(
        channel=payload["channel"],
        customer_id=payload["customer_id"],
        text=payload["draft_reply"],
        thread_id=payload.get("thread_id"),
    )
    when = datetime.now(tz=timezone.utc).isoformat()
    await q.edit_message_reply_markup(reply_markup=sent_keyboard(when))
    if send_result.get("ok"):
        await q.message.reply_text(
            f"✓ Sent to {payload['customer_name']} on {payload['channel']}."
        )
    else:
        await q.message.reply_text(
            f"⚠ Approve recorded but outbound failed: "
            f"{send_result.get('error', 'unknown error')}. "
            f"Decision is approved in the audit log."
        )

    # Post-approval chains by decision kind.
    kind = payload.get("kind")
    if kind == "marketing":
        loop = await launch_marketing_plan(payload)
        if loop.get("skipped"):
            return
        campaigns_run = sum(
            1 for c in (loop.get("campaigns") or [])
            if c.get("create", {}).get("ok")
        )
        leads_routed = sum(
            sum(1 for r in (c.get("routes") or []) if r.get("ok"))
            for c in (loop.get("campaigns") or [])
        )
        adjustments = loop.get("adjustments") or []
        adjustments_made = sum(1 for a in adjustments if a.get("ok"))
        posts = loop.get("instagram_posts") or []
        posts_published = sum(
            1 for p in posts if p.get("publish", {}).get("ok")
        )
        bits: list[str] = []
        if leads_routed:
            bits.append(f"{leads_routed} lead(s) routed")
        if adjustments_made:
            bits.append(f"{adjustments_made} campaign(s) adjusted")
        elif adjustments:
            bits.append(f"{len(adjustments)} campaign(s) reviewed (no changes)")
        if posts:
            bits.append(f"{posts_published}/{len(posts)} IG post(s) published"
                        if posts_published else
                        "IG posts queued but did not publish — see /audit")
        suffix = (", " + ", ".join(bits)) if bits else ""
        if loop.get("ok"):
            await q.message.reply_text(
                f"📣 Closed loop ran: {campaigns_run} campaign(s) launched, "
                f"leads generated, owner report logged{suffix}. See /audit.",
            )
        else:
            await q.message.reply_text(
                f"⚠ Marketing closed loop had failures{suffix} — check /audit. "
                f"Decision is approved either way."
            )
        return

    # POS + kitchen handoff for intake/custom decisions.
    fulfill = await fulfill_approved(payload)
    if fulfill.get("skipped"):
        return
    order_id = fulfill.get("order_id")
    ticket_id = fulfill.get("ticket_id")
    if fulfill.get("ok") and order_id:
        msg = f"📦 POS order `{order_id}` confirmed"
        if ticket_id:
            msg += f", kitchen ticket `{ticket_id}` queued."
        else:
            msg += " (kitchen ticket failed — check /audit)."
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    elif not fulfill.get("ok"):
        steps = fulfill.get("steps") or []
        failures = [s for s in steps if not s.get("ok")]
        first = failures[0] if failures else {}
        await q.message.reply_text(
            f"⚠ POS/kitchen chain partially failed at "
            f"`{first.get('step', '?')}`: {first.get('error', 'unknown')}. "
            f"See /audit.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def _handle_kitchen_action(q, action: str, ticket_id: str, arg2: str = "") -> None:
    """Drive the kitchen lifecycle from inline-keyboard taps."""
    h = hosted_mcp()
    if action == "kit_accept":
        try:
            await h.call_tool("kitchen_accept_ticket", {"ticketId": ticket_id})
        except MCPError as exc:
            await q.message.reply_text(f"⚠ accept failed for {ticket_id}: {exc}")
            return
        audit_write(
            event_id=f"kit_acc_{ticket_id}",
            kind="kitchen_ticket_accepted",
            payload={"ticket_id": ticket_id, "by": "owner"},
        )
        await q.edit_message_reply_markup(reply_markup=kitchen_keyboard(ticket_id, "accepted"))
        await q.message.reply_text(f"✅ Ticket `{ticket_id}` accepted.",
                                    parse_mode=ParseMode.MARKDOWN)
        return

    if action == "kit_ready":
        try:
            await h.call_tool(
                "kitchen_mark_ready",
                {"ticketId": ticket_id,
                 "pickupNote": "Ready on counter — owner-marked from Telegram."},
            )
        except MCPError as exc:
            await q.message.reply_text(f"⚠ mark-ready failed for {ticket_id}: {exc}")
            return
        audit_write(
            event_id=f"kit_rdy_{ticket_id}",
            kind="kitchen_ticket_ready",
            payload={"ticket_id": ticket_id, "by": "owner"},
        )
        await q.edit_message_reply_markup(reply_markup=kitchen_keyboard(ticket_id, "ready"))
        await q.message.reply_text(f"🔥 Ticket `{ticket_id}` marked ready.",
                                    parse_mode=ParseMode.MARKDOWN)
        return

    if action == "kit_reject":
        await q.edit_message_reply_markup(
            reply_markup=kitchen_reject_reason_keyboard(ticket_id),
        )
        return

    if action == "kit_rreason":
        reason_code = arg2 or "other"
        label = dict(KITCHEN_REJECT_REASONS).get(reason_code, reason_code)
        try:
            await h.call_tool(
                "kitchen_reject_ticket",
                {"ticketId": ticket_id, "reason": label},
            )
        except MCPError as exc:
            await q.message.reply_text(f"⚠ reject failed for {ticket_id}: {exc}")
            return
        audit_write(
            event_id=f"kit_rej_{ticket_id}",
            kind="kitchen_ticket_rejected",
            payload={"ticket_id": ticket_id, "reason_code": reason_code,
                     "reason_label": label},
        )
        await q.edit_message_reply_markup(reply_markup=kitchen_keyboard(ticket_id, "rejected"))
        await q.message.reply_text(
            f"❌ Ticket `{ticket_id}` rejected — *{label}*.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def _handle_reject_with_reason(q, decision: dict, *, reason_code: str) -> None:
    payload = decision["payload"]
    decision_id = payload["decision_id"]
    label = dict(REJECT_REASONS).get(reason_code, reason_code)
    decision_set_status(decision_id, "rejected", rejection_reason=reason_code)
    audit_write(
        event_id=f"dec_rej_{decision_id}",
        kind="decision_rejected",
        payload={
            "decision_id": decision_id,
            "reason_code": reason_code,
            "reason_label": label,
        },
    )
    when = datetime.now(tz=timezone.utc).isoformat()
    await q.edit_message_reply_markup(reply_markup=sent_keyboard(when))
    await q.message.reply_text(
        f"❌ Rejected — reason: *{label}*. The customer will not get the draft reply.\n"
        f"Decision id `{decision_id}` is logged.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Bootstrap ───────────────────────────────────────────────────────────────


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("custom", cmd_custom))
    app.add_handler(CommandHandler("care", cmd_care))
    app.add_handler(CommandHandler("marketing", cmd_marketing))
    app.add_handler(CommandHandler("plan_marketing", cmd_plan_marketing))
    app.add_handler(CommandHandler("reports", cmd_reports))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("replay", cmd_replay))
    app.add_handler(CommandHandler("audit", cmd_audit))
    app.add_handler(CommandHandler("kitchen", cmd_kitchen))
    app.add_handler(CommandHandler("kitchen_summary", cmd_kitchen_summary))
    app.add_handler(CallbackQueryHandler(on_callback))
    # Catch-all for free-text the owner sends — gentle nudge.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))


async def _on_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        await _reject_non_owner(update)
        return
    await update.message.reply_text(
        "I work via tap-only buttons. Try /help to see what I can do.",
        reply_markup=main_menu_keyboard(),
    )


__all__ = ["register_handlers"]
