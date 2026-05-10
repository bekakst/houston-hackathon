"""Kitchen MCP client — feasibility checks for custom cakes."""

from __future__ import annotations

from datetime import datetime, timezone

from happycake.mcp.local_data import cake_by_slug, load_kitchen_calendar
from happycake.mcp.inventory import available, alternatives
from happycake.schemas import CakeSpec


def feasibility(spec: CakeSpec, *, now: datetime | None = None) -> dict:
    """Check whether a CakeSpec fits lead-time AND kitchen capacity."""
    now = now or datetime.now(tz=timezone.utc)
    issues: list[str] = []
    suggestions: list[dict] = []

    base_slug = spec.base_cake_slug or "custom"
    cake = cake_by_slug(base_slug)
    if not cake:
        return {"ok": False, "reason": f"unknown base cake slug: {base_slug}"}

    if spec.deadline:
        deadline = spec.deadline if spec.deadline.tzinfo else spec.deadline.replace(tzinfo=timezone.utc)
        hours_out = (deadline - now).total_seconds() / 3600
        if hours_out < cake.lead_time_hours:
            issues.append(
                f"deadline is {hours_out:.0f}h away; {cake.display_name()} needs "
                f"{cake.lead_time_hours}h. Pickup or delivery must move."
            )
            suggestions = alternatives(base_slug, deadline.date())

    if spec.tiers > max(cake.tier_options):
        issues.append(
            f"{cake.display_name()} supports up to {max(cake.tier_options)} tier(s); "
            f"requested {spec.tiers}. Custom cake or fewer tiers required."
        )

    if spec.deadline:
        avail = available(base_slug, spec.deadline.date())
        if not avail["available"]:
            issues.append(f"no remaining capacity on {avail['date']} for {cake.display_name()}.")
            if not suggestions:
                suggestions = alternatives(base_slug, spec.deadline.date())

    if spec.fulfillment == "delivery" and spec.delivery_zone not in cake.delivery_zones:
        issues.append(
            f"delivery zone '{spec.delivery_zone}' is outside our zones for "
            f"{cake.display_name()}: {', '.join(cake.delivery_zones)}."
        )

    return {
        "ok": not issues,
        "issues": issues,
        "suggestions": suggestions,
        "lead_time_hours_required": cake.lead_time_hours,
        "max_tiers": max(cake.tier_options),
    }


def calendar_summary(days: int = 7) -> dict:
    cal = load_kitchen_calendar()
    bookings = cal.get("bookings", {})
    keys = sorted(bookings.keys())[:days]
    return {
        "defaults": cal["defaults"],
        "next_days": [{"date": k, **bookings[k]} for k in keys],
    }
