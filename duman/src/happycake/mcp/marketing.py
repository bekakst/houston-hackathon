"""Marketing MCP client — channel allocation and campaign metrics."""

from __future__ import annotations


CHANNEL_DEFAULTS = {
    "meta_ads": {
        "min_usd": 50,
        "ctr_pct": 1.6,
        "cpc_usd": 1.10,
        "conversion_pct": 3.0,
        "audience": "Sugar Land women 25-65, family-oriented",
    },
    "google_ads": {
        "min_usd": 50,
        "ctr_pct": 4.0,
        "cpc_usd": 2.20,
        "conversion_pct": 6.0,
        "audience": "intent-based local search 'cake near me Sugar Land'",
    },
    "boosted_posts": {
        "min_usd": 25,
        "ctr_pct": 0.8,
        "cpc_usd": 0.60,
        "conversion_pct": 1.5,
        "audience": "existing followers; reach amplification",
    },
    "review_generation": {
        "min_usd": 0,
        "incentive_per_review_usd": 5,
        "expected_reviews_per_50_usd": 8,
    },
    "retention_sms": {
        "min_usd": 0,
        "send_cost_usd": 0.02,
        "open_rate_pct": 35,
        "conversion_pct": 8.0,
        "audience": "permission-based prior customers",
    },
}


def channel_defaults() -> dict:
    return CHANNEL_DEFAULTS
