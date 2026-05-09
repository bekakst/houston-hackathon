"""Catalog MCP client.

Returns Cake objects with allergens, lead-time, sizes, photos. The agent
reads ingredient and allergen lists ONLY from this client — never from
the LLM's own knowledge.
"""

from __future__ import annotations

from happycake.mcp.local_data import cake_by_slug, load_catalog
from happycake.schemas import Cake


def list_all() -> list[Cake]:
    return load_catalog()


def get(slug: str) -> Cake | None:
    return cake_by_slug(slug)


def search_by_serves(serves: int) -> list[Cake]:
    return [c for c in load_catalog() if c.serves_min <= serves <= c.serves_max]


def search_by_allergen_safe(allergen: str) -> list[Cake]:
    """Return cakes that do NOT list this allergen.

    Note: this is informational only. Allergen-question messages must always
    escalate to the owner before any commitment is made (brand book hard rule).
    """
    a = allergen.lower()
    return [c for c in load_catalog() if a not in c.allergens]


def ingredient_ledger(slug: str) -> dict:
    """All facts about a single cake, structured for LLM grounding."""
    cake = cake_by_slug(slug)
    if not cake:
        return {}
    return {
        "slug": cake.slug,
        "name": cake.name,
        "ingredients": cake.ingredients,
        "allergens": cake.allergens,
        "halal_friendly": cake.halal_friendly,
        "vegan": cake.vegan,
        "lead_time_hours": cake.lead_time_hours,
        "tier_options": cake.tier_options,
        "delivery_zones": cake.delivery_zones,
    }
