"""Pricing MCP client.

Every dollar amount the agent says out loud must come from quote(). The
agent's system prompt enforces "no price not from pricing.quote", and the
brand_critic blocks any draft that contains a USD amount with no matching
evidence row in the Reply.
"""

from __future__ import annotations

from happycake.mcp.local_data import cake_by_slug, load_policies


def quote(cake_slug: str, size_label: str, *, fulfillment: str = "pickup",
          delivery_zone: str | None = None, quantity: int = 1) -> dict:
    """Return a structured price quote with all components."""
    cake = cake_by_slug(cake_slug)
    if not cake:
        return {"ok": False, "reason": f"unknown cake slug: {cake_slug}"}
    size = next((s for s in cake.sizes if s.label == size_label), None)
    if not size:
        return {
            "ok": False,
            "reason": f'size "{size_label}" unavailable for {cake.display_name()}',
            "available_sizes": [s.label for s in cake.sizes],
        }
    base = round(size.price_usd * quantity, 2)
    delivery_fee = 0.0
    delivery_min_order = 0.0
    policies = load_policies()
    if fulfillment == "delivery":
        zone = next(
            (z for z in policies["delivery_zones"] if z["slug"] == delivery_zone),
            None,
        )
        if not zone:
            return {
                "ok": False,
                "reason": f'delivery zone "{delivery_zone}" not served',
                "available_zones": [z["slug"] for z in policies["delivery_zones"]],
            }
        delivery_fee = float(zone["fee_usd"])
        delivery_min_order = float(zone["min_order_usd"])
        if base < delivery_min_order:
            return {
                "ok": False,
                "reason": f"delivery minimum is ${delivery_min_order:.0f} for zone "
                          f"{zone['name']}; current order is ${base:.2f}",
                "delivery_min_order": delivery_min_order,
            }
    total = round(base + delivery_fee, 2)
    return {
        "ok": True,
        "cake_slug": cake.slug,
        "cake_display": cake.display_name(),
        "size_label": size_label,
        "quantity": quantity,
        "base_usd": base,
        "delivery_fee_usd": delivery_fee,
        "total_usd": total,
        "weight_g": size.weight_g * quantity,
        "fulfillment": fulfillment,
        "delivery_zone": delivery_zone,
    }


def margin(cake_slug: str, size_label: str, *, total_usd: float) -> dict:
    """Approximate margin for an internal owner-facing card."""
    # Coarse cost ratios per cake category — matches sandbox seed assumptions.
    cake = cake_by_slug(cake_slug)
    if not cake:
        return {"margin_usd": 0.0, "margin_pct": 0.0}
    cost_ratio = 0.55 if cake.slug == "custom" else 0.42
    cost = round(total_usd * cost_ratio, 2)
    margin_usd = round(total_usd - cost, 2)
    margin_pct = round((margin_usd / total_usd) * 100, 1) if total_usd else 0.0
    return {"margin_usd": margin_usd, "margin_pct": margin_pct, "cost_usd": cost}
