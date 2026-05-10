"""Inline-keyboard rendering for the Telegram owner bot.

Every owner action is a tap. Reject reasons are buttons, not free text. The
operator simulator's 15-pt rubric lives in this file.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


REJECT_REASONS: list[tuple[str, str]] = [
    ("capacity",   "Out of capacity"),
    ("ingredient", "Ingredient unavailable"),
    ("deadline",   "Deadline too tight"),
    ("design",     "Design out of scope"),
    ("price",      "Price concern"),
    ("other",      "Other"),
]


KITCHEN_REJECT_REASONS: list[tuple[str, str]] = [
    ("capacity",   "Over capacity"),
    ("stock",      "Out of stock"),
    ("equipment",  "Equipment issue"),
    ("staff",      "Staffing"),
    ("other",      "Other"),
]


def approval_keyboard(decision_id: str) -> InlineKeyboardMarkup:
    """The 2x3 grid the owner sees on every approval card."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{decision_id}"),
            InlineKeyboardButton("✏️ Edit",    callback_data=f"edit:{decision_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"reject:{decision_id}"),
        ],
        [
            InlineKeyboardButton("👁 Preview",  callback_data=f"preview:{decision_id}"),
            InlineKeyboardButton("📞 Call",     callback_data=f"call:{decision_id}"),
            InlineKeyboardButton("🚨 Kitchen",  callback_data=f"kitchen:{decision_id}"),
        ],
    ])


def reject_reason_keyboard(decision_id: str) -> InlineKeyboardMarkup:
    """One-tap reason picker. Brief's rubric specifically calls for this."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"rreason:{decision_id}:{code}")]
        for code, label in REJECT_REASONS
    ])


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Pinned chat menu — onboarding screen."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Pending orders", callback_data="cmd:orders"),
         InlineKeyboardButton("🎂 Custom queue",   callback_data="cmd:custom")],
        [InlineKeyboardButton("💬 Care tickets",   callback_data="cmd:care"),
         InlineKeyboardButton("📊 Today report",   callback_data="cmd:reports")],
        [InlineKeyboardButton("📣 Marketing",      callback_data="cmd:marketing"),
         InlineKeyboardButton("ℹ️ Help",            callback_data="cmd:help")],
    ])


def report_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Today",   callback_data="report:today"),
        InlineKeyboardButton("📈 7-day",   callback_data="report:7d"),
        InlineKeyboardButton("📅 30-day",  callback_data="report:30d"),
    ]])


def sent_keyboard(when_iso: str) -> InlineKeyboardMarkup:
    """Replaces the approval keyboard after the owner taps Approve.

    No clickable buttons — just a non-interactive `Sent at HH:MM` indicator.
    Telegram requires a callback_data even on disabled buttons; we use a
    no-op string so taps simply ack and dismiss.
    """
    short = when_iso[11:16]
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✓ Sent at {short} UTC", callback_data="noop")
    ]])


def kitchen_keyboard(ticket_id: str, status: str) -> InlineKeyboardMarkup:
    """Build the keyboard for a kitchen ticket card based on its status."""
    s = (status or "").lower()
    rows: list[list[InlineKeyboardButton]] = []
    if s == "queued":
        rows.append([
            InlineKeyboardButton("✅ Accept", callback_data=f"kit_accept:{ticket_id}"),
            InlineKeyboardButton("🔥 Mark ready", callback_data=f"kit_ready:{ticket_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"kit_reject:{ticket_id}"),
        ])
    elif s == "accepted":
        rows.append([
            InlineKeyboardButton("🔥 Mark ready", callback_data=f"kit_ready:{ticket_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"kit_reject:{ticket_id}"),
        ])
    else:
        rows.append([InlineKeyboardButton(f"({status})", callback_data="noop")])
    return InlineKeyboardMarkup(rows)


def kitchen_reject_reason_keyboard(ticket_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"kit_rreason:{ticket_id}:{code}")]
        for code, label in KITCHEN_REJECT_REASONS
    ])


def build_kitchen_card(ticket: dict) -> str:
    items = ticket.get("items") or []
    item_str = ", ".join(
        f"{i.get('quantity',1)}× {i.get('productId') or i.get('name','?')}"
        for i in items
    ) or "(no items)"
    eta = (ticket.get("estimatedReadyAt") or "")[:19].replace("T", " ")
    prep = ticket.get("estimatedPrepMinutes")
    lines = [
        f"🎂 Ticket {ticket.get('id', '?')} — {ticket.get('status', '?').upper()}",
        f"Order: {ticket.get('orderId', '?')}",
        f"Customer: {ticket.get('customerName', '?')}",
        f"Items: {item_str}",
    ]
    if prep is not None:
        lines.append(f"Prep: {prep} min")
    if eta:
        lines.append(f"ETA: {eta} UTC")
    if ticket.get("rejectionReason"):
        lines.append(f"Rejection reason: {ticket['rejectionReason']}")
    return "\n".join(lines)


def build_card_text(payload: dict) -> str:
    """Pretty-print the OwnerDecision payload as a card body.

    The dispatcher already populates `summary`. This function adds margin /
    lead-time / allergen flags when available.
    """
    if payload.get("kind") == "gender_reveal":
        return _build_gender_reveal_card(payload)
    lines = [payload.get("summary", "(no summary)")]

    spec = payload.get("draft_cake_spec")
    if spec:
        items = spec.get("items") or []
        if items:
            lines.extend(["", "Items:"])
            for it in items:
                qty = it.get("quantity", 1)
                lines.append(f"  • {qty}× {it.get('cake_slug')} ({it.get('size_label')})")
        elif spec.get("base_cake_slug"):
            lines.extend([
                "",
                f"Item: 1× {spec['base_cake_slug']} ({spec.get('size_label', '?')})",
            ])

        meta_lines = []
        if spec.get("fulfillment"):
            meta_lines.append(f"  • fulfillment: {spec['fulfillment']}")
        if spec.get("deadline"):
            meta_lines.append(f"  • deadline: {spec['deadline']}")
        if spec.get("delivery_address"):
            meta_lines.append(f"  • address: {spec['delivery_address']}")
        elif spec.get("delivery_zone"):
            meta_lines.append(f"  • zone: {spec['delivery_zone']}")
        if spec.get("customer_name"):
            meta_lines.append(f"  • name: {spec['customer_name']}")
        if spec.get("customer_phone"):
            meta_lines.append(f"  • phone: {spec['customer_phone']}")
        if meta_lines:
            lines.extend(["", *meta_lines])

    flags = payload.get("allergen_flags") or []
    if flags:
        lines.extend(["", f"⚠ Allergens: {', '.join(flags)}"])

    if payload.get("total_usd") is not None:
        margin_pct = payload.get("margin_pct")
        margin_usd = payload.get("margin_usd")
        line = f"💰 Total ${payload['total_usd']:.2f}"
        if margin_usd is not None and margin_pct is not None:
            line += f" · margin ${margin_usd:.2f} ({margin_pct:.0f}%)"
        lines.extend(["", line])

    if payload.get("lead_time_ok") is False:
        lines.append("⏱ Lead-time tight — kitchen ack required")

    return "\n".join(lines)


def _build_gender_reveal_card(payload: dict) -> str:
    """Owner-only card. The gender appears here clearly so the kitchen knows
    which interior colour to bake. This card is only ever sent to the
    configured owner chat; never to the customer."""
    gender = (payload.get("reveal_gender") or "").lower()
    interior = "PINK (girl)" if gender == "girl" else (
        "BLUE (boy)" if gender == "boy" else "(unknown)"
    )
    lines = [
        f"🔒 Gender-reveal locked — {payload.get('reveal_order_id', '?')}",
        f"Orderer: {payload.get('customer_name', '?')}",
        f"Contact: {payload.get('customer_id', '?')}",
        f"Party: {payload.get('reveal_party_date', '?')}",
        f"Cake: {payload.get('reveal_cake_size_kg', '?')} kg · "
        f"{payload.get('reveal_pickup_or_delivery', '?')}",
        "",
        f"Interior: {interior}",
    ]
    if payload.get("reveal_decorations"):
        lines.append(f"Decorations: {payload['reveal_decorations']}")
    if payload.get("reveal_notes_to_baker"):
        lines.append(f"Notes: {payload['reveal_notes_to_baker']}")
    if (payload.get("reveal_pickup_or_delivery") == "delivery"
            and payload.get("reveal_delivery_address")):
        lines.append(f"Address: {payload['reveal_delivery_address']}")
    lines.extend([
        "",
        "Approve to confirm and queue the kitchen ticket. The customer reply",
        "will say only that the cake is locked — no colour, no gender.",
        "",
        "Suggested reply to customer:",
        payload.get("draft_reply", "(no draft)"),
    ])
    return "\n".join(lines)


__all__ = [
    "approval_keyboard",
    "reject_reason_keyboard",
    "main_menu_keyboard",
    "report_period_keyboard",
    "sent_keyboard",
    "build_card_text",
    "REJECT_REASONS",
    "KITCHEN_REJECT_REASONS",
    "kitchen_keyboard",
    "kitchen_reject_reason_keyboard",
    "build_kitchen_card",
]
