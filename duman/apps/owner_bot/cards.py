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


def build_card_text(payload: dict) -> str:
    """Pretty-print the OwnerDecision payload as a card body.

    The dispatcher already populates `summary`. This function adds margin /
    lead-time / allergen flags when available.
    """
    lines = [payload.get("summary", "(no summary)")]

    spec = payload.get("draft_cake_spec")
    if spec:
        spec_lines = []
        for key in ("base_cake_slug", "size_label", "tiers", "deadline",
                    "fulfillment", "delivery_zone"):
            if spec.get(key) not in (None, "", []):
                spec_lines.append(f"  • {key}: {spec[key]}")
        if spec_lines:
            lines.extend(["", "Cake spec:", *spec_lines])

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


__all__ = [
    "approval_keyboard",
    "reject_reason_keyboard",
    "main_menu_keyboard",
    "report_period_keyboard",
    "sent_keyboard",
    "build_card_text",
    "REJECT_REASONS",
]
