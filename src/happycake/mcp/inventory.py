"""Inventory MCP client. Reads the kitchen calendar and per-cake daily caps."""

from __future__ import annotations

from datetime import date

from happycake.mcp.local_data import cake_by_slug, load_kitchen_calendar


def available(cake_slug: str, target_date: date) -> dict:
    cake = cake_by_slug(cake_slug)
    if not cake:
        return {"ok": False, "reason": f"unknown cake slug: {cake_slug}"}
    cal = load_kitchen_calendar()
    iso = target_date.isoformat()
    booked = cal.get("bookings", {}).get(iso, {})
    defaults = cal["defaults"]
    cap_daily = defaults["daily_classics_per_day"]
    cap_24h = defaults["twenty_four_hour_classics_per_day"]
    cap_custom = defaults["custom_jobs_per_day"]
    if cake.slug == "custom":
        used = booked.get("custom", 0)
        cap = cap_custom
    elif cake.lead_time_hours <= 4:
        used = booked.get("daily_classics", 0)
        cap = cap_daily
    else:
        used = booked.get("twenty_four_hour", 0)
        cap = cap_24h
    remaining = max(cap - used, 0)
    return {
        "ok": True,
        "cake_slug": cake_slug,
        "date": iso,
        "remaining": remaining,
        "capacity": cap,
        "available": remaining > 0,
    }


def alternatives(cake_slug: str, target_date: date, serves: int | None = None) -> list[dict]:
    """Lead-time-aware substitution: in-stock cakes within +/-20% serving size."""
    from happycake.mcp.local_data import load_catalog

    requested = cake_by_slug(cake_slug)
    if not requested:
        return []
    out: list[dict] = []
    for c in load_catalog():
        if c.slug in (cake_slug, "custom"):
            continue
        avail = available(c.slug, target_date)
        if not avail["available"]:
            continue
        if serves and not (c.serves_min <= serves <= c.serves_max):
            continue
        out.append({
            "cake_slug": c.slug,
            "cake_display": c.display_name(),
            "lead_time_hours": c.lead_time_hours,
            "remaining": avail["remaining"],
        })
    return out[:5]
