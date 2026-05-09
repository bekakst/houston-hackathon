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
    REJECT_REASONS,
    approval_keyboard,
    build_card_text,
    main_menu_keyboard,
    reject_reason_keyboard,
    report_period_keyboard,
    sent_keyboard,
)
from apps.owner_bot.outbound import send_to_customer
from happycake.mcp import orders as orders_mcp
from happycake.storage import (
    audit_recent,
    audit_write,
    decision_get,
    decision_list_pending,
    decision_set_status,
    now_iso,
)

log = logging.getLogger(__name__)


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
    text = (
        "*HappyCake owner bot — quick reference*\n\n"
        "/orders — pending standard orders\n"
        "/custom — pending custom-cake requests\n"
        "/care — open complaints / status / refunds\n"
        "/reports — today / 7-day / 30-day summary\n"
        "/marketing — review and approve marketing drafts\n"
        "/status `<order_id>` — quick lookup\n"
        "/replay `<thread_id>` — see the agent's reasoning trace\n"
        "/audit — last 20 audit events\n"
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


async def cmd_reports(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    pending = decision_list_pending(limit=50)
    overview = _format_pending_count(pending)
    await update.message.reply_text(
        f"{overview}\n\nPick a reporting period:",
        reply_markup=report_period_keyboard(),
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
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


async def cmd_audit(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
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
    app.add_handler(CommandHandler("reports", cmd_reports))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("replay", cmd_replay))
    app.add_handler(CommandHandler("audit", cmd_audit))
    app.add_handler(CallbackQueryHandler(on_callback))
    # Catch-all for free-text the owner sends — gentle nudge.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))


async def _on_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "I work via tap-only buttons. Try /help to see what I can do.",
        reply_markup=main_menu_keyboard(),
    )


__all__ = ["register_handlers"]
